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
    assert len(test) == 100 and len(dev) == 17   # testset: 텍스트 85 + 사진 15
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


def test_latency_scores():
    scores = run_eval._latency_scores([2000, 60000, 64000, 70000, 116000])
    assert scores == {"latency_avg_s": 62.4, "latency_p50_s": 64.0, "latency_p95_s": 116.0, "latency_total_s": 312}
    assert run_eval._latency_scores([])["latency_avg_s"] is None


def test_build_photo_matches_app_photo_flow(monkeypatch):
    """사진 채점은 앱 코드(ChatApp._analyze_photo)를 그대로 부른다. 그 반환 모양이 바뀌면 여기서 잡는다
    (통합 테스트 10/30: (응답, state, 질문 사용 여부) 3개로 바뀌어 사진 15문항이 모두 error 였음)"""
    from chatbot.core.schemas import AnswerResponse
    from chatbot.ui.gradio_app import ChatApp

    def fake_analyze(self, image, state, question=""):
        return AnswerResponse(status="retake", input_type="other_photo", message="다시"), state, False

    monkeypatch.setattr(ChatApp, "_analyze_photo", fake_analyze)
    response = run_eval._build_photo(pipeline=None)(b"img", {"user": None, "equipment": None})
    assert response["status"] == "retake" and response["input_type"] == "other_photo"


@pytest.mark.parametrize("argv", [["--limit", "0"], ["--set", "nope"]])
def test_main_rejects_bad_args(argv):
    with pytest.raises(SystemExit):
        run_eval.main(argv)


# ------------------------------------------------------------------------------
# 사진 문항 (ChatApp 사진 흐름 대신 가짜 함수)
# ------------------------------------------------------------------------------
def nameplate_reply(model):
    return {"status": "nameplate", "input_type": "nameplate_photo",
            "image": {"kind": "nameplate", "confidence": 0.99, "nameplate": {"matched_model": model}}}


class FakePhoto:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.images = []

    def __call__(self, image, state):
        self.images.append(image)
        return self.responses.pop(0)


def photo_case(case_id, path, **kw):
    return Case(case_id=case_id, question="[사진]", input_type="nameplate_photo", image_path=path, **kw)


def test_photo_cases_use_photo_flow_and_score_nameplate():
    photo = FakePhoto(nameplate_reply("138-ACC"), nameplate_reply(None), {"status": "retake"})
    evaluator = Evaluator(pipeline=FakePipeline(), photo=photo)
    cases = [photo_case("p1", "eval/images/nameplate/138-ACC_front_img.png", gold_model="138-ACC", group="#4"),
             photo_case("p2", "eval/images/nameplate/L400_front_img.png", gold_model="L400", group="#4"),
             photo_case("p3", "eval/images/nameplate/L500_side_img.png", should_abstain=True, group="#5-2")]
    results = [evaluator.run_case(c) for c in cases]
    assert len(photo.images) == 3 and evaluator.pipeline.calls == []   # 텍스트 파이프라인은 부르지 않음
    scores = evaluator.score_nameplate(results)
    assert scores["nameplate_ok"] == 0.5 and scores["nameplate_n"] == 2
    assert run_eval._wrong(results[1]) == ["nameplate"]
    assert run_eval._wrong(results[2]) == []                         # 재촬영 안내 = 모름으로 인정
    abstention = evaluator.score_abstention(results)
    assert abstention["abstain_ok"] == 1.0 and abstention["over_refusal_rate"] == 0.0


def test_score_groups_counts_cases_without_wrong_items():
    evaluator = Evaluator(pipeline=FakePipeline(answered(), not_found()))
    results = [evaluator.run_case(ts_case(group="#1")), evaluator.run_case(ts_case(group="#1"))]
    scores = evaluator.score_groups(results)
    assert scores["group_#1_n"] == 2
    assert scores["group_#1_acc"] == round(sum(not run_eval._wrong(r) for r in results) / 2, 3)


def test_case_row_records_not_found_stage():
    stages = [
        {"status": "not_found", "trace": {"latency_ms": {"understand": 1}}},
        {"status": "not_found", "trace": {"latency_ms": {"search": 1}, "top_score": 0.3, "threshold": 0.55}},
        {"status": "not_found", "trace": {"latency_ms": {"search": 1, "generate": 1}}},
        {"status": "not_found", "trace": {"latency_ms": {"search": 1, "generate": 1, "verify": 1},
                                          "removed_cause_ids": [f"{KB}#c1"]}},
        answered(),
    ]
    evaluator = Evaluator(pipeline=FakePipeline(*stages))
    rows = [run_eval._case_row(evaluator.run_case(ts_case())) for _ in stages]
    assert [r["nf_stage"] for r in rows] == ["out_of_scope", "threshold", "generate", "verify", ""]
    assert rows[1]["top_score"] == 0.3 and rows[3]["removed_cause_ids"] == f"{KB}#c1"


def test_case_row_records_generate_fail():
    response = {"status": "not_found", "trace": {"latency_ms": {"search": 1, "generate": 1}, "generate_fail": "llm_not_found"}}
    evaluator = Evaluator(pipeline=FakePipeline(response))
    row = run_eval._case_row(evaluator.run_case(ts_case()))
    assert row["nf_stage"] == "generate" and row["generate_fail"] == "llm_not_found"
