"""
eval/run_eval.py Evaluator 테스트 (WBS 4.1.2 · 기능 V-01)

- RagPipeline 대신 가짜 파이프라인을 넣어 API·DB·색인 없이 채점 규칙만 확인한다
실행: python -m pytest tests/run_eval_test.py -v
"""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
sys.modules["run_eval"] = run_eval   # dataclass 가 모듈을 찾을 수 있게 먼저 등록
_spec.loader.exec_module(run_eval)
Case, Evaluator, Result = run_eval.Case, run_eval.Evaluator, run_eval.Result

KB = "TS-HYTROL138ACC-T01-04"


def ts_case(**kw):
    return Case(case_id="tc_1", question="138-ACC 모터가 뜨거워요", qtype="troubleshooting", gold_kb_ids=[KB],
                gold_causes=[f"{KB}#c1", f"{KB}#c2"], gold_sources=[{"doc": "hytrol_138acc", "page": 22}],
                needs_safety=True, **kw)


def answered(hits=(KB,), causes=(f"{KB}#c1",), sources=(("hytrol_138acc", 22),), safety=1):
    return {"status": "answered", "causes": [{"id": c} for c in causes],
            "sources": [{"doc_id": d, "page": p} for d, p in sources], "kb_ids": [KB],
            "safety": [{"rule_id": "HY-LOTO"}] * safety, "trace": {"hits": [{"id": h} for h in hits]}}


def not_found(hits=()):
    return {"status": "not_found", "message": "확인되지 않음", "trace": {"hits": [{"id": h} for h in hits]}}


class FakePipeline:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def answer(self, question, state, input_type):
        self.calls.append((question, state, input_type))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


# ------------------------------------------------------------------------------
def test_load_testset_reads_real_files():
    """실제 평가셋 칸 이름(gold_kb · gold_source · expect_not_found)이 Case 로 옮겨지는지"""
    evaluator = Evaluator(pipeline=FakePipeline())
    test = evaluator.load_testset(str(ROOT / "eval" / "testset.jsonl"))
    dev = evaluator.load_testset(str(ROOT / "eval" / "devset.jsonl"))
    assert len(test) == 85 and len(dev) == 17
    first_ts = next(c for c in test if c.gold_kb_ids)
    assert first_ts.gold_causes and first_ts.gold_sources[0].keys() == {"doc", "page"}
    assert any(c.should_abstain for c in test) and any(c.needs_safety for c in test)


def test_run_case_uses_fresh_state_and_survives_errors():
    pipeline = FakePipeline(answered(), RuntimeError("boom"))
    evaluator = Evaluator(pipeline=pipeline)
    first = evaluator.run_case(ts_case())
    second = evaluator.run_case(ts_case())
    assert first.response["status"] == "answered" and first.latency_ms >= 0
    assert second.response["status"] == "error" and "boom" in second.response["message"]
    question, state, input_type = pipeline.calls[0]
    assert state["equipment"] is None and input_type == "text"   # 문항마다 설비 필터 없는 새 상태


def test_score_kb_top1_and_recall():
    other = "TS-HYTROL138ACC-T01-01"
    chunk = "hytrol_138acc:p22:s3:c0"
    results = [Result(ts_case(), answered(hits=(KB, other)), 1),            # 1위 정답
               Result(ts_case(), answered(hits=(chunk, KB, other)), 1),     # 청크 1위, KB 중 1위는 정답
               Result(ts_case(), answered(hits=(other, chunk, KB)), 1),     # 다른 KB 가 먼저 → Recall@5 만
               Result(ts_case(), not_found(hits=(other,)), 1),              # 못 찾음
               Result(Case("mt", "점검 주기?"), answered(), 1)]              # gold_kb 없음 → 대상 아님
    scores = Evaluator().score_kb(results)
    assert scores["kb_n"] == 4
    assert scores["kb_top1"] == 0.5 and scores["kb_recall@5"] == 0.75


def test_score_causes_sums_over_cases():
    results = [Result(ts_case(), answered(causes=(f"{KB}#c1", f"{KB}#c9")), 1),   # 1개 맞힘, 1개 틀림
               Result(ts_case(), answered(causes=(f"{KB}#c1", f"{KB}#c2")), 1)]   # 2개 다 맞힘
    scores = Evaluator().score_causes(results)
    assert scores["cause_recall"] == 0.75 and scores["cause_precision"] == 0.75


def test_score_causes_precision_none_when_nothing_answered():
    scores = Evaluator().score_causes([Result(ts_case(), not_found(), 1)])
    assert scores["cause_recall"] == 0.0 and scores["cause_precision"] is None


def test_score_sources_page_and_doc():
    results = [Result(ts_case(), answered(sources=(("hytrol_138acc", 22),)), 1),
               Result(ts_case(), answered(sources=(("hytrol_138acc", 23),)), 1)]   # 문서만 맞음
    scores = Evaluator().score_sources(results)
    assert scores["source_page"] == 0.5 and scores["source_doc"] == 1.0


def test_score_abstention():
    abstain = Case("oos", "점심 메뉴 추천", qtype="out_of_scope", should_abstain=True)
    results = [Result(abstain, not_found(), 1),                      # 모름 처리 성공
               Result(ts_case(), not_found(), 1),                    # 과잉 거절
               Result(ts_case(), answered(safety=0), 1),             # 답했지만 안전 문구 없음
               Result(ts_case(), {"status": "error"}, 1)]
    scores = Evaluator().score_abstention(results)
    assert scores["abstain_ok"] == 1.0
    assert scores["over_refusal_rate"] == round(1 / 3, 3)
    assert scores["safety_included"] == 0.0 and scores["safety_n"] == 1   # 답한 문항만 대상
    assert scores["errors"] == 1


def test_write_report_files(tmp_path):
    evaluator = Evaluator(results_dir=tmp_path, version="v1")
    results = [Result(ts_case(), answered(causes=(f"{KB}#c1",)), 12.3)]
    evaluator.write_report({"kb_top1": 1.0, "cause_precision": None}, results)
    metrics, cases = sorted(tmp_path.glob("v1_*.csv"), key=lambda p: p.name.endswith("_cases.csv"))
    with open(metrics, encoding="utf-8-sig") as f:
        assert list(csv.reader(f)) == [["metric", "value"], ["kb_top1", "1.0"], ["cause_precision", ""]]
    with open(cases, encoding="utf-8-sig") as f:
        (row,) = list(csv.DictReader(f))
    assert row["case_id"] == "tc_1" and row["wrong"] == "causes"   # c2 를 빠뜨림


@pytest.mark.parametrize("argv", [["--limit", "0"], ["--set", "nope"]])
def test_main_rejects_bad_args(argv):
    with pytest.raises(SystemExit):
        run_eval.main(argv)
