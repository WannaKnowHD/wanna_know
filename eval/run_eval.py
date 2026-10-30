"""
==============================================================================
 eval/run_eval.py : 평가셋 자동 채점  (담당: 김민재 / WBS 4.1.2 / 기능 V-01·V-02)
==============================================================================

[실행]  프로젝트 최상단 폴더에서, 가상환경을 켠 상태로
  python eval/run_eval.py                              # 평가셋 85문항, 버전 이름 current
  python eval/run_eval.py --set dev --version v1       # 개발셋 17문항, 결과 파일 이름 v1_…
  python eval/run_eval.py --set dev --limit 3          # 앞 3문항만 (동작 확인용)
  python eval/run_eval.py --no-log                     # DB chat_logs 에 남기지 않음

[채점 기준]  정답은 testset.jsonl · devset.jsonl 의 gold_* 칸
  - KB      : Top-1 = 검색 결과(trace.hits) 중 순위가 가장 높은 KB 가 정답 KB (본문 청크는 건너뜀)
              Recall@5 = 상위 5개 안에 정답 KB
              생성 단계와 떼어 검색 품질만 보려고 "답변"이 아니라 "검색 결과"로 잰다
  - 원인    : 답변 원인 id 와 gold_causes 비교 → 재현율·정밀도 (문항을 모두 합쳐서 계산)
  - 출처    : 답변 출처의 (문서, 쪽)이 gold_source 중 하나와 같으면 정답
  - 모름    : 모름 처리율 = 모름이 정답인 문항을 not_found 로 답한 비율
              과잉 거절률 = 답이 있는 문항을 not_found 로 답한 비율
              안전 문구 포함률 = needs_safety 문항 중 답변한 것에 안전 문구가 붙은 비율
  - 시간    : 문항별 answer 실행 시간의 평균·p50·p95·합계 (초)
  각 지표는 그 정답 칸이 있는 문항만 센다 (예: gold_kb 가 빈 점검 질문은 KB 정확도에서 빠짐)

[결과]  eval/results/{버전}_{날짜시각}.csv        지표 (지표, 값, 맞은 수, 대상 수)
        eval/results/{버전}_{날짜시각}_cases.csv  문항별 결과. wrong 칸이 비어 있으면 모두 맞음
  평가 로그는 chat_logs 에 source=eval 로 남겨 대시보드 숫자에는 섞이지 않는다 (DB 연결이 안 되면 건너뜀)
==============================================================================
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # 프로젝트 루트에서 chatbot 패키지 찾기

from chatbot.core.schemas import KB_ID_PATTERN  # noqa: E402

KB_ID = re.compile(KB_ID_PATTERN)  # 검색 결과 id 중 KB 인 것 (청크 id 는 "fenner_belt:p35:…" 모양)
SETS = {"test": ROOT / "eval" / "testset.jsonl", "dev": ROOT / "eval" / "devset.jsonl"}
RESULTS_DIR = ROOT / "eval" / "results"
TOP_K = 5  # Recall@5


@dataclass
class Case:
    """평가 문항 1개 (testset.jsonl 한 줄)."""
    case_id: str
    question: str
    input_type: str = "text"
    gold_kb_ids: list[str] = field(default_factory=list)
    gold_causes: list[str] = field(default_factory=list)
    gold_sources: list[dict] = field(default_factory=list)
    should_abstain: bool = False
    qtype: str = ""              # 유형별로 결과를 볼 때 씀 (troubleshooting · maintenance · procedure · out_of_scope)
    needs_safety: bool = False   # 안전 문구 포함률의 대상 문항


@dataclass
class Result:
    """문항 1개 실행 결과."""
    case: Case
    response: dict
    latency_ms: float


class Evaluator:
    """평가셋을 RagPipeline.answer 로 실행하고 채점한다. (담당: 김민재)"""

    def __init__(self, pipeline=None, log: bool = True, results_dir: Path = RESULTS_DIR, version: str = "current"):
        """
        받는 것:
          pipeline    : answer(question, state, input_type) 가 있는 객체. None 이면 첫 run_case 때 실제 부품으로 만든다
                        (테스트는 가짜 파이프라인을 넣어 API·DB 없이 돌린다)
          log         : True 면 DB chat_logs 에 source=eval 로 남긴다 (실제 부품을 만들 때만 의미 있음)
          results_dir : 결과 CSV 폴더
          version     : 결과 파일 이름 앞부분 (v0~v3 비교용)
        """
        self.pipeline = pipeline
        self.log = log
        self.results_dir = Path(results_dir)
        self.version = version

    def load_testset(self, path: str):
        """
        평가셋(testset.jsonl) 90문항과 개발셋 20문항을 읽음

        jsonl 칸 이름 → Case: gold_kb → gold_kb_ids, gold_source → gold_sources, expect_not_found → should_abstain
        Returns: list[Case]
        """
        cases = []
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not row.get("case_id") or not row.get("question"):
                    raise ValueError(f"{path}:{line_no} case_id 또는 question 이 비어 있음")
                cases.append(Case(
                    case_id=row["case_id"], question=row["question"], input_type=row.get("input_type") or "text",
                    gold_kb_ids=list(row.get("gold_kb") or []), gold_causes=list(row.get("gold_causes") or []),
                    gold_sources=list(row.get("gold_source") or []),
                    should_abstain=bool(row.get("expect_not_found")), qtype=row.get("qtype") or "",
                    needs_safety=bool(row.get("needs_safety")),
                ))
        return cases

    def run_case(self, case: Case):
        """
        문항 1개를 rag.answer로 실행하고 응답·시간 기록

        질문마다 새 대화 상태(설비 필터 없음)로 실행한다. 문항끼리 영향을 주지 않게 하려는 것.
        answer 가 예외를 내도 평가 전체를 멈추지 않고 그 문항만 error 로 기록한다.
        Returns: Result
        """
        if self.pipeline is None:
            self.pipeline = _build_pipeline(self.log)
        state = {"user": None, "admin_mode": False, "equipment": None}
        started = time.perf_counter()
        try:
            response = self.pipeline.answer(case.question, state, case.input_type)
        except Exception as e:  # noqa: BLE001 — 한 문항 실패로 나머지 채점을 막지 않음
            response = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        return Result(case=case, response=response, latency_ms=(time.perf_counter() - started) * 1000)

    def score_kb(self, results: list[Result]):
        """
        Top-1 KB 정확도, 검색 Recall@5 계산

        대상: gold_kb_ids 가 있는 문항
        Returns: dict {"kb_top1": 비율, "kb_recall@5": 비율, "kb_n": 대상 수, "kb_top1_hits", "kb_recall@5_hits"}
        """
        checks = [_checks(r) for r in results if r.case.gold_kb_ids]
        return _rates("kb", len(checks), top1=sum(c["kb_top1"] for c in checks),
                      **{"recall@5": sum(c["kb_recall5"] for c in checks)})

    def score_causes(self, results: list[Result]):
        """
        원인 재현율·정밀도 계산 (원인 id 기준)

        대상: gold_causes 가 있는 문항. 문항별 평균이 아니라 전체 합으로 계산한다
          재현율 = 맞힌 원인 수 / 정답 원인 수,  정밀도 = 맞힌 원인 수 / 답변한 원인 수
        답변한 원인이 하나도 없으면 정밀도는 None (0 으로 두면 "틀린 원인을 말했다"로 오해되므로)
        Returns: dict
        """
        hit = gold = predicted = 0
        for r in results:
            if not r.case.gold_causes:
                continue
            answer_ids = set(_cause_ids(r.response))
            hit += len(answer_ids & set(r.case.gold_causes))
            gold += len(set(r.case.gold_causes))
            predicted += len(answer_ids)
        return {"cause_recall": _ratio(hit, gold), "cause_precision": _ratio(hit, predicted),
                "cause_hits": hit, "cause_gold": gold, "cause_predicted": predicted}

    def score_sources(self, results: list[Result]):
        """
        출처(문서, 페이지) 정확도 계산

        대상: gold_sources 가 있는 문항. 답변 출처 중 하나라도 (문서, 쪽)이 정답과 같으면 맞음
        문서만 맞은 비율도 함께 낸다 (쪽 번호 기준이 어긋난 것과 문서를 잘못 찾은 것을 구분하려고)
        Returns: dict
        """
        checks = [_checks(r) for r in results if r.case.gold_sources]
        return _rates("source", len(checks), page=sum(c["source_ok"] for c in checks),
                      doc=sum(c["source_doc_ok"] for c in checks))

    def score_abstention(self, results: list[Result]):
        """
        모름 처리율, 과잉 거절률, 안전 문구 포함률 계산

          모름 처리율      : should_abstain 문항 중 not_found 로 답한 비율 (높을수록 좋음)
          과잉 거절률      : 답이 있는 문항 중 not_found 로 답한 비율 (낮을수록 좋음)
          안전 문구 포함률 : needs_safety 문항 중 answered 인 것에 안전 문구가 있는 비율
                             (답을 못 한 문항은 안전 문구가 붙을 수 없어서 대상에서 뺀다)
          오류 수          : status=error 인 문항 수
        Returns: dict
        """
        abstain = [r for r in results if r.case.should_abstain]
        answerable = [r for r in results if not r.case.should_abstain]
        safety = [r for r in results if r.case.needs_safety and _status(r) == "answered"]
        scores = {}
        scores.update(_rates("abstain", len(abstain), ok=sum(_status(r) == "not_found" for r in abstain)))
        scores.update(_rates("over_refusal", len(answerable), rate=sum(_status(r) == "not_found" for r in answerable)))
        scores.update(_rates("safety", len(safety), included=sum(bool(r.response.get("safety")) for r in safety)))
        scores["errors"] = sum(_status(r) == "error" for r in results)
        return scores

    def write_report(self, scores: dict, results: list[Result]):
        """
        지표 CSV와 오답 목록을 results/{버전}_{날짜}.csv로 저장 (v0~v3 버전 비교용)

        {버전}_{날짜시각}.csv       : 지표 이름, 값
        {버전}_{날짜시각}_cases.csv : 문항별 결과. wrong 칸에 틀린 항목 이름(kb_top1, source …)
        같은 날 여러 번 돌려도 덮어쓰지 않게 시각까지 붙인다. 엑셀에서 한글이 깨지지 않게 utf-8-sig 로 저장
        Returns: None
        """
        self.results_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self.version}_{datetime.now():%Y%m%d_%H%M%S}"
        with open(self.results_dir / f"{stem}.csv", "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            writer.writerows([[name, "" if value is None else value] for name, value in scores.items()])
        with open(self.results_dir / f"{stem}_cases.csv", "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CASE_COLUMNS)
            writer.writeheader()
            writer.writerows(_case_row(r) for r in results)
        print(f"[run_eval] 저장: {self.results_dir / stem}.csv, {stem}_cases.csv")


# ------------------------------------------------------------------------------
# 내부 함수
# ------------------------------------------------------------------------------
CASE_COLUMNS = ["case_id", "qtype", "question", "status", "wrong", "top1_hit", "top1_kb", "gold_kb", "kb_ids", "gold_causes",
                "cause_ids", "gold_source", "sources", "safety", "latency_ms", "message"]


def _build_pipeline(log: bool):
    """실제 부품으로 RagPipeline 을 만든다 (앱과 같은 설정·색인·KB·용어집·안전 규칙)."""
    from chatbot.core.config import Settings
    from chatbot.core.safety import SafetyRules
    from chatbot.pipeline import RagPipeline, load_kb
    from chatbot.rag.glossary import Glossary
    from chatbot.rag.llm import LLMClient
    from chatbot.rag.retriever import Retriever
    from chatbot.rag.validator import AnswerValidator

    settings = Settings.load()
    llm = LLMClient(settings)
    return RagPipeline(
        llm, retriever=Retriever.from_settings(settings, llm), validator=AnswerValidator(), safety=SafetyRules(),
        request_logger=_request_logger(settings) if log else None,
        glossary=Glossary().load_glossary(str(ROOT / "data" / "glossary.csv")), kb=load_kb(settings.kb_dir),
        log_source="eval",
    )


def _request_logger(settings):
    """DB 에 연결되면 RequestLogger, 안 되면 None (평가는 로그 없이 계속)"""
    from chatbot.core.logger import RequestLogger
    from chatbot.db.db import Database

    try:
        return RequestLogger(Database().connect(settings))
    except Exception as e:  # noqa: BLE001
        print(f"[run_eval] DB 연결 실패 → chat_logs 에 남기지 않고 계속: {type(e).__name__}: {e}")
        return None


def _status(result: Result) -> str:
    return result.response.get("status") or "error"


def _cause_ids(response: dict) -> list[str]:
    return [c.get("id") for c in response.get("causes") or [] if c.get("id")]


def _hit_ids(response: dict) -> list[str]:
    """검색 결과 id (리랭킹 후 순위 순서). 검색 전에 끝난 응답은 빈 목록"""
    return [h.get("id") for h in (response.get("trace") or {}).get("hits") or []]


def _top_kb(hits: list[str]) -> str | None:
    """검색 결과 중 순위가 가장 높은 KB id. 본문·표 청크가 1위여도 KB 정확도는 KB 끼리 비교하려고 건너뛴다"""
    return next((h for h in hits if KB_ID.match(h or "")), None)


def _sources(response: dict) -> list[tuple[str, int]]:
    return [(s.get("doc_id"), s.get("page")) for s in response.get("sources") or []]


def _checks(result: Result) -> dict:
    """문항 1개의 채점 결과. score_* 와 문항별 CSV 가 같은 기준을 쓰게 한 곳에서 계산한다."""
    case, response = result.case, result.response
    hits = _hit_ids(response)
    gold_pages = {(s.get("doc"), s.get("page")) for s in case.gold_sources}
    gold_docs = {doc for doc, _ in gold_pages}
    answered = _sources(response)
    return {
        "kb_top1": _top_kb(hits) in case.gold_kb_ids,
        "kb_recall5": any(h in case.gold_kb_ids for h in hits[:TOP_K]),
        "causes_all": set(case.gold_causes) <= set(_cause_ids(response)),
        "source_ok": any(s in gold_pages for s in answered),
        "source_doc_ok": any(doc in gold_docs for doc, _ in answered),
        "abstain_ok": (_status(result) == "not_found") == case.should_abstain,
        "safety_ok": bool(response.get("safety")),
    }


def _wrong(result: Result) -> list[str]:
    """그 문항에서 틀린 항목 이름 목록 (대상이 아닌 항목은 넣지 않음)"""
    case, c = result.case, _checks(result)
    wrong = []
    if _status(result) == "error":
        wrong.append("error")
    if not c["abstain_ok"]:
        wrong.append("abstain" if case.should_abstain else "over_refusal")
    if case.gold_kb_ids and not c["kb_top1"]:
        wrong.append("kb_top1")
    if case.gold_causes and not c["causes_all"]:
        wrong.append("causes")
    if case.gold_sources and not c["source_ok"]:
        wrong.append("source")
    if case.needs_safety and _status(result) == "answered" and not c["safety_ok"]:
        wrong.append("safety")
    return wrong


def _case_row(result: Result) -> dict:
    case, response = result.case, result.response
    hits = _hit_ids(response)
    return {
        "case_id": case.case_id, "qtype": case.qtype, "question": case.question, "status": _status(result),
        "wrong": " ".join(_wrong(result)), "top1_hit": hits[0] if hits else "", "top1_kb": _top_kb(hits) or "",
        "gold_kb": ";".join(case.gold_kb_ids), "kb_ids": ";".join(response.get("kb_ids") or []),
        "gold_causes": ";".join(case.gold_causes), "cause_ids": ";".join(_cause_ids(response)),
        "gold_source": ";".join(f"{s.get('doc')} p{s.get('page')}" for s in case.gold_sources),
        "sources": ";".join(f"{doc} p{page}" for doc, page in _sources(response)),
        "safety": len(response.get("safety") or []), "latency_ms": round(result.latency_ms),
        "message": response.get("message") or "",
    }


def _latency_scores(latencies_ms: list[float]) -> dict:
    """문항별 걸린 시간(ms) → 평균·p50·p95·합계(초). 버전끼리 속도도 비교하려고 지표 CSV에 같이 남긴다.

    백분위는 가장 가까운 순위 방식: 정렬해서 앞에서 ceil(비율 × 개수)번째 값 (17개면 p95 = 17번째 = 최댓값)
    """
    if not latencies_ms:
        return {"latency_avg_s": None, "latency_p50_s": None, "latency_p95_s": None, "latency_total_s": None}
    ordered = sorted(latencies_ms)

    def percentile(q):
        return ordered[max(math.ceil(q * len(ordered)) - 1, 0)] / 1000

    return {"latency_avg_s": round(sum(ordered) / len(ordered) / 1000, 1),
            "latency_p50_s": round(percentile(0.5), 1), "latency_p95_s": round(percentile(0.95), 1),
            "latency_total_s": round(sum(ordered) / 1000)}


def _ratio(hit: int, total: int) -> float | None:
    """비율 (소수 셋째 자리). 대상이 없으면 None — 0.0 과 '잴 수 없음'을 구분하려고"""
    return round(hit / total, 3) if total else None


def _rates(prefix: str, total: int, **hits: int) -> dict:
    """{prefix}_{이름}: 비율, {prefix}_{이름}_hits: 맞은 수, {prefix}_n: 대상 수"""
    scores = {}
    for name, hit in hits.items():
        scores[f"{prefix}_{name}"] = _ratio(hit, total)
        scores[f"{prefix}_{name}_hits"] = hit
    scores[f"{prefix}_n"] = total
    return scores


def main(argv=None):
    parser = argparse.ArgumentParser(description="평가셋 자동 채점")
    parser.add_argument("--set", choices=sorted(SETS), default="test", help="test(평가셋) 또는 dev(개발셋)")
    parser.add_argument("--version", default="current", help="결과 파일 이름 앞부분 (예: v0, v1)")
    parser.add_argument("--limit", type=int, help="앞에서부터 이 수만큼만 실행")
    parser.add_argument("--no-log", action="store_true", help="DB chat_logs 에 남기지 않음")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 은 1 이상이어야 합니다.")

    evaluator = Evaluator(log=not args.no_log, version=args.version)
    cases = evaluator.load_testset(str(SETS[args.set]))[:args.limit]
    results = []
    for i, case in enumerate(cases, start=1):
        result = evaluator.run_case(case)
        results.append(result)
        print(f"[{i}/{len(cases)}] {case.case_id} {_status(result)} {' '.join(_wrong(result)) or 'OK'}"
              f" ({result.latency_ms / 1000:.1f}s)")

    scores = {"set": args.set, "version": args.version, "cases": len(results)}
    for score in (evaluator.score_kb, evaluator.score_causes, evaluator.score_sources, evaluator.score_abstention):
        scores.update(score(results))
    scores.update(_latency_scores([r.latency_ms for r in results]))
    print("\n[지표]")
    for name, value in scores.items():
        if not name.endswith("_hits"):
            print(f"  {name}: {value}")
    evaluator.write_report(scores, results)


if __name__ == "__main__":
    main()
