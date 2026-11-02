"""실행: python eval/search_report.py → 리랭킹 전후 비교 · 기준값 정하기 (WBS 3.4.4 · 3.4.5)

개발셋(eval/devset.jsonl)으로 검색만 돌린다.
    python eval/search_report.py                 # 질문 이해(understand_question)의 검색어
    python eval/search_report.py --use-question  # 질문 이해가 아직 없을 때: 질문 원문

결과는 settings.results_dir에 저장한다.
    search_dev_{날짜}.csv  문항별 정답 순위(리랭킹 전·후), 1위 점수, 리랭킹 시간
    threshold_{날짜}.csv   기준값 후보별 모름 처리율 · 과잉 거절률
화면에 나오는 표는 "기준값 결정 근거"로 그대로 붙인다. 평가셋(testset)은 조정에 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

# 프로젝트 루트에서 chatbot 패키지 찾기
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.rag.retriever import CANDIDATE_K, TOP_K, Retriever  # noqa: E402

TARGET_ABSTENTION = (
    0.80  # 모름 처리율 목표: 범위 밖 질문 중 "확인되지 않음" 비율 (기획서 13.4)
)
TARGET_OVER_REFUSAL = 0.10  # 과잉 거절률 목표: 답이 있는 질문 중 "확인되지 않음" 비율
THRESHOLDS = [round(step * 0.01, 2) for step in range(101)]


def load_cases(path: str | Path) -> list[dict]:
    """개발셋(jsonl)에서 텍스트 질문만 읽는다. 키는 #60 데이터 형식(gold_kb · gold_source · expect_not_found)."""
    cases = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            case = json.loads(line)
            if case.get("input_type", "text") == "text":
                cases.append(case)
    return cases


def gold_rank(hits: list, case: dict) -> int | None:
    """정답 KB id 또는 정답 (문서, 쪽)이 처음 나온 순위 (1부터). 없으면 None."""
    gold_ids = set(case.get("gold_kb") or [])
    gold_pages = {
        (source.get("doc"), source.get("page"))
        for source in case.get("gold_source") or []
    }
    for rank, hit in enumerate(hits, start=1):
        if hit.id in gold_ids or (hit.doc_id, hit.page) in gold_pages:
            return rank
    return None


def compare_case(
    retriever: Retriever, case: dict, queries: list[str], qtype: str | None
) -> dict:
    """문항 1개를 "RRF까지만" 순위와 실제 검색(리랭킹 포함) 순위로 비교한다."""
    lists = []
    for query in queries:
        lists.append(retriever.bm25_search(query, CANDIDATE_K, None))
        lists.append(retriever.vector_search(query, CANDIDATE_K, None))
    before = retriever.rrf_merge(lists)[:TOP_K]

    started = time.perf_counter()
    after, _ = retriever.search(
        queries, None, qtype
    )  # 앱과 같은 경로라 1위 점수를 그대로 쓴다
    search_ms = (time.perf_counter() - started) * 1000
    return {
        "case_id": case.get("case_id"),
        "question": case.get("question"),
        "should_abstain": bool(case.get("expect_not_found")),
        "queries": " | ".join(queries),
        "rank_before": gold_rank(before, case),
        "rank_after": gold_rank(after, case),
        "top_score": round(after[0].score, 4) if after else None,
        "search_ms": round(search_ms),
        "top5_before": " / ".join(hit.id for hit in before),
        "top5_after": " / ".join(f"{hit.id}({hit.score:.2f})" for hit in after),
    }


def summarize(rows: list[dict]) -> dict:
    """리랭킹 전후 검색 1위 정답률 · Recall@5와 검색 시간."""
    answerable = [row for row in rows if not row["should_abstain"]]
    times = [row["search_ms"] for row in rows]
    return {
        "cases": len(rows),
        "answerable": len(answerable),
        "top1_before": _share(answerable, lambda row: row["rank_before"] == 1),
        "top1_after": _share(answerable, lambda row: row["rank_after"] == 1),
        "recall5_before": _share(
            answerable, lambda row: row["rank_before"] is not None
        ),
        "recall5_after": _share(answerable, lambda row: row["rank_after"] is not None),
        "search_ms_p50": statistics.median(times) if times else None,
        "search_ms_p95": _p95(times),
    }


def sweep_thresholds(
    rows: list[dict], thresholds: list[float] = THRESHOLDS
) -> list[dict]:
    """기준값 후보마다 모름 처리율과 과잉 거절률을 계산한다."""
    abstain = [row for row in rows if row["should_abstain"]]
    answerable = [row for row in rows if not row["should_abstain"]]
    table = []
    for threshold in thresholds:

        def refused(row: dict, threshold: float = threshold) -> bool:
            return row["top_score"] is None or row["top_score"] < threshold

        table.append(
            {
                "threshold": threshold,
                "abstention_rate": _share(abstain, refused),
                "over_refusal_rate": _share(answerable, refused),
            }
        )
    return table


def choose_threshold(table: list[dict]) -> dict:
    """목표(모름 처리율 0.80 이상, 과잉 거절률 0.10 이하)를 지키는 값 중 가운데 값을 고른다.

    지키는 값이 없으면 (모름 처리율 - 과잉 거절률)이 가장 큰 값을 고르고 meets_target=False.
    점수가 같은 후보가 여러 개면 가운데 값을 고른다: 개발셋이 20문항뿐이라 경계에 붙은 값은
    문항 하나만 달라져도 결과가 뒤집힌다.
    """

    def gap(row: dict) -> float:
        return (row["abstention_rate"] or 0.0) - (row["over_refusal_rate"] or 0.0)

    feasible = [
        row
        for row in table
        if (row["abstention_rate"] or 0.0) >= TARGET_ABSTENTION
        and (row["over_refusal_rate"] or 0.0) <= TARGET_OVER_REFUSAL
    ]
    candidates = feasible or table
    best = max(gap(row) for row in candidates)
    tied = [row for row in candidates if gap(row) == best]
    return {**tied[len(tied) // 2], "meets_target": bool(feasible)}


def format_report(summary: dict, table: list[dict], choice: dict) -> str:
    """문서에 붙일 Markdown 표를 만든다."""
    lines = [
        f"개발셋 {summary['cases']}문항 (답이 있는 문항 {summary['answerable']}개)",
        "",
        "| 지표 | 리랭킹 전 (RRF) | 리랭킹 후 |",
        "| --- | --- | --- |",
        f"| 검색 1위 정답률 | {_pct(summary['top1_before'])} | {_pct(summary['top1_after'])} |",
        f"| Recall@5 | {_pct(summary['recall5_before'])} | {_pct(summary['recall5_after'])} |",
        "",
        f"검색 시간(리랭킹 포함) p50 {summary['search_ms_p50']} ms · "
        f"p95 {summary['search_ms_p95']} ms",
        "",
        "| 기준값 | 모름 처리율 | 과잉 거절률 |",
        "| --- | --- | --- |",
    ]
    for row in table:
        # 0.05 간격으로 줄이고, 고른 값은 간격과 상관없이 넣는다
        if (
            round(row["threshold"] * 100) % 5 == 0
            or row["threshold"] == choice["threshold"]
        ):
            lines.append(
                f"| {row['threshold']:.2f} | {_pct(row['abstention_rate'])} "
                f"| {_pct(row['over_refusal_rate'])} |"
            )
    verdict = "목표를 지킴" if choice["meets_target"] else "목표를 지키는 값이 없음"
    lines += ["", f"고른 기준값: **{choice['threshold']:.2f}** ({verdict})"]
    return "\n".join(lines)


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(
        path, "w", encoding="utf-8-sig", newline=""
    ) as file:  # 엑셀에서 한글이 깨지지 않게
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="개발셋으로 리랭킹 전후 비교 · 기준값 정하기"
    )
    parser.add_argument("--devset", default="eval/devset.jsonl")
    parser.add_argument(
        "--use-question", action="store_true", help="질문 이해 없이 질문 원문으로 검색"
    )
    args = parser.parse_args()

    # 설정·OpenAI 클라이언트는 실제로 돌릴 때만 불러온다 (위 함수들은 테스트에서 가짜로 확인)
    from chatbot.core.config import Settings
    from chatbot.rag.llm import LLMClient

    settings = Settings.load()
    llm = LLMClient(settings)
    retriever = Retriever.from_settings(settings, llm)
    understand = None if args.use_question else _load_understand(llm)
    cases = load_cases(args.devset)
    if not cases:
        raise SystemExit(f"{args.devset}에 텍스트 문항이 없습니다")

    rows = []
    for case in cases:
        if understand is None:
            queries, qtype = [case["question"]], None
        else:
            queries, qtype = understand(case["question"])
        rows.append(compare_case(retriever, case, queries, qtype))

    summary = summarize(rows)
    table = sweep_thresholds(rows)
    choice = choose_threshold(table)
    print(format_report(summary, table, choice))

    stamp = date.today().strftime("%Y%m%d")
    write_csv(rows, Path(settings.results_dir) / f"search_dev_{stamp}.csv")
    write_csv(table, Path(settings.results_dir) / f"threshold_{stamp}.csv")
    print(f"\n저장: {settings.results_dir}")
    if choice["meets_target"]:
        print(
            f".env의 NOT_FOUND_THRESHOLD를 {choice['threshold']:.2f}로 바꾸면 됩니다."
        )


def _load_understand(llm):
    """앱과 같은 경로(질문 이해 → 검색어 변환, 3.5.1)로 (영문 검색어, 질문 유형)을 만드는 함수."""
    from chatbot.pipeline import RagPipeline
    from chatbot.rag.glossary import Glossary

    pipeline = RagPipeline(llm)
    glossary = Glossary().load_glossary(str(Path(__file__).resolve().parents[1] / "data" / "glossary.csv"))

    def understand(question: str):
        info = pipeline.understand_question(question, {})
        return pipeline.rewrite_query(question, glossary), info.qtype

    return understand


def _share(rows: list[dict], predicate) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if predicate(row)) / len(rows)


def _p95(values: list[float]) -> float | None:
    if len(values) < 2:
        return values[0] if values else None
    return statistics.quantiles(values, n=20)[18]


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


if __name__ == "__main__":
    main()
