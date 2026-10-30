"""RagPipeline.answer 통합 동작 확인 (WBS 3.5.6). 실제 OpenAI · 검색 색인 · MySQL 없이 검사함.

- LLM 은 정해 둔 답을 차례로 돌려주는 가짜, 검색은 정해 둔 Hit 를 돌려주는 가짜를 쓴다
- KB(kb/) · 안전 규칙(data/safety_rules.json) · 로그(RequestLogger + sqlite 메모리 DB)는 진짜를 쓴다
- build_prompt 는 tests/pipeline_prompt_test.py 에서 따로 검사하므로 여기서는 받은 값만 기록하는 가짜로 바꾼다

실행 (프로젝트 루트에서):
    python -m pytest tests/pipeline_answer_test.py -v
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # sqlite_db

from sqlite_db import SqliteDb

from chatbot.core.logger import RequestLogger
from chatbot.core.safety import SafetyRules
from chatbot.core.schemas import AnswerResponse, Hit, User
from chatbot.pipeline import (
    MSG_ERROR,
    MSG_NOT_FOUND,
    MSG_OUT_OF_SCOPE,
    RagPipeline,
    load_kb,
)

KB_ID = "TS-HYTROL138ACC-T01-04"           # 138-ACC 모터·감속기 과열 (원인 3개)
MT_ID = "MT-HYTROL138ACC-T03-02"           # 모터 온도 점검 (매주)
ENGINEER = User(employee_id="e1", name="엔지니어", role="user")

UNDERSTAND_TS = "유형: troubleshooting\n설비: 138-ACC\n부품: 모터\n증상: 모터 과열"
UNDERSTAND_MT = "유형: maintenance\n설비: 138-ACC\n부품: 모터\n증상: 없음"
UNDERSTAND_OUT = "유형: out_of_scope\n설비: 없음\n부품: 없음\n증상: 없음"
QUERIES = "motor overheating\nreducer overheating"

# LLM 이 KB 원인·조치(영어)를 한국어로 옮긴 답 (id 는 KB 그대로, 조치 개수는 KB 와 같게). c9 는 KB 에 없는 지어낸 id
KO_CAUSES = {
    f"{KB_ID}#c1": {"id": f"{KB_ID}#c1", "cause": "컨베이어 과부하", "actions": ["컨베이어 용량을 확인하고 권장 수준으로 부하를 줄이세요."]},
    f"{KB_ID}#c2": {"id": f"{KB_ID}#c2", "cause": "모터 저전압", "actions": ["전기 기사에게 점검과 조치를 맡기세요."]},
    f"{KB_ID}#c9": {"id": f"{KB_ID}#c9", "cause": "모터 베어링 손상", "actions": ["베어링을 교체하세요."]},
}


# ------------------------------------------------------------------------------ 가짜 부품
class FakeLLM:
    """chat 을 부를 때마다 replies 를 앞에서부터 하나씩 돌려준다.
    str → 텍스트 답, dict → LLMAnswer 답(parsed), None → 형식이 깨진 답, Exception → 호출 실패"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, model=None, json_schema=None):
        self.calls.append(json_schema)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            return {"text": reply, "parsed": None, "model": "fake", "latency_ms": 1}
        return {"text": "{}", "parsed": reply, "model": "fake", "latency_ms": 1}


class FakeRetriever:
    """search 는 정해 둔 hits 를 돌려주고, 받은 값을 기록한다. is_not_found 는 Retriever 와 같은 규칙"""

    def __init__(self, hits, threshold=0.55, error=None):
        self.hits, self.threshold, self.error = hits, threshold, error
        self.calls = []

    def search(self, queries, equipment, qtype):
        self.calls.append({"queries": queries, "equipment": equipment, "qtype": qtype})
        if self.error:
            raise self.error
        return self.hits, False

    def is_not_found(self, hits):
        return not hits or hits[0].score < self.threshold


def ts_hit(score=0.9):
    """고장 KB 검색 결과 (색인의 KB 글 형식 = prompts.KB_TROUBLESHOOTING · KB_CAUSE).
    글에 원인 id 와 "조치 순서" 가 있어야 verify_causes 가 id · 조치 개수를 확인한다"""
    text = ("적용 설비: 138-ACC, 138-LRC, 138-LRS, 138-LRSS\n"
            "증상: Motor or reducer overheating.\n"
            f"- 원인 id {KB_ID}#c1: Conveyor is overloaded.\n"
            "  조치 순서: Check capacity of conveyor and reduce load to recommended level.\n"
            f"- 원인 id {KB_ID}#c2: Low voltage to motor.\n"
            "  조치 순서: Have electrician check and correct as necessary.\n"
            f"- 원인 id {KB_ID}#c3: Low lubricant level in reducer.\n"
            "  조치 순서: Relubricate per manufacturer's recommendations.")
    return Hit(id=KB_ID, text=text, score=score, doc_id="hytrol_138acc", page=22,
               metadata={"doc_id": "hytrol_138acc", "page": 22, "chunk_type": "kb", "kb_type": "troubleshooting"})


def mt_hit():
    return Hit(id=MT_ID, text="MOTOR: Check Temperature. Interval: weekly.", score=0.8, doc_id="hytrol_138acc",
               page=24, metadata={"doc_id": "hytrol_138acc", "page": 24, "chunk_type": "kb", "kb_type": "maintenance"})


def ts_answer(cause_ids=(f"{KB_ID}#c1", f"{KB_ID}#c2"), quote="Conveyor is overloaded.", not_found=False):
    return {"not_found": not_found, "summary": "과부하와 모터 저전압을 먼저 확인하세요.",
            "causes": [KO_CAUSES[c] for c in cause_ids], "actions": [], "sources": [{"ref_id": KB_ID, "quote": quote}]}


def mt_answer():
    return {"not_found": False, "summary": "모터 온도는 매주 점검하세요.", "causes": [],
            "actions": [{"text": "모터 온도를 매주 확인하세요.", "ref_id": MT_ID}],
            "sources": [{"ref_id": MT_ID, "quote": "MOTOR: Check Temperature."}]}


# ------------------------------------------------------------------------------ 준비
@pytest.fixture(scope="module")
def kb():
    return load_kb(ROOT / "kb")


@pytest.fixture(scope="module")
def safety():
    return SafetyRules(ROOT / "data" / "safety_rules.json")


@pytest.fixture
def db():
    return SqliteDb()


@pytest.fixture
def make(kb, safety, db, monkeypatch):
    """RagPipeline 만들기. build_prompt 는 받은 값만 기록하는 가짜로 바꾼다"""
    prompts = []

    def fake_build_prompt(self, question, hits, question_type=None, equipment=None):
        prompts.append({"hits": [h.id for h in hits], "question_type": question_type, "equipment": equipment})
        return [{"role": "system", "content": "s"}, {"role": "user", "content": question}]

    monkeypatch.setattr(RagPipeline, "build_prompt", fake_build_prompt)

    def _make(llm, retriever, **kwargs):
        pipeline = RagPipeline(llm, retriever=retriever, safety=safety, request_logger=RequestLogger(db), kb=kb,
                               **kwargs)
        pipeline.prompts = prompts
        return pipeline
    return _make


def logs(db):
    return db.fetch("SELECT * FROM chat_logs ORDER BY log_id")


def state():
    return {"user": ENGINEER, "admin_mode": False, "equipment": None}


# ------------------------------------------------------------------------------ 정상 답변
def test_troubleshooting_answer_uses_checked_causes(make):
    llm = FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer())
    out = make(llm, FakeRetriever([ts_hit()])).answer("138-ACC 모터가 뜨거워요", state(), "text")

    AnswerResponse.model_validate(out)                       # 응답 스키마에 맞음
    assert out["status"] == "answered" and out["question_type"] == "troubleshooting"
    assert [c["id"] for c in out["causes"]] == [f"{KB_ID}#c1", f"{KB_ID}#c2"]
    assert out["causes"][0]["cause"] == "컨베이어 과부하"                        # LLM 이 한국어로 옮긴 문장
    assert out["causes"][1]["actions"] == ["전기 기사에게 점검과 조치를 맡기세요."]
    assert out["actions"] == []                                                 # 고장 답변은 causes 로만
    assert out["kb_ids"] == [KB_ID]
    assert out["equipment"] == {"model": "138-ACC", "doc_ids": [], "source": "question"}


def test_sources_use_doc_id_as_title(make):
    out = make(FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer()), FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    source = out["sources"][0]
    assert source["doc_id"] == source["doc_title"] == "hytrol_138acc"          # 팀 결정: 문서 이름 = doc_id
    assert source["page"] == 22 and source["chunk_type"] == "kb"


def test_safety_attached_from_rules_file(make):
    out = make(FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer()), FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    rule_ids = [note["rule_id"] for note in out["safety"]]
    assert rule_ids and rule_ids[0] == "HY-LOTO"                                # 고장 답변 → LOTO 먼저
    assert "HY-ELECTRICIAN" in rule_ids                                          # 저전압(electric) 원인


def test_maintenance_answer_keeps_actions(make):
    llm = FakeLLM(UNDERSTAND_MT, QUERIES, mt_answer())
    out = make(llm, FakeRetriever([mt_hit()])).answer("138-ACC 모터 온도 점검 주기", state(), "text")
    assert out["status"] == "answered" and out["question_type"] == "maintenance"
    assert out["actions"] == [{"text": "모터 온도를 매주 확인하세요.", "ref_id": MT_ID}]
    assert out["causes"] == [] and out["kb_ids"] == [MT_ID] and out["safety"]


def test_trace_records_steps(make):
    out = make(FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer()), FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    trace = out["trace"]
    assert {"understand", "search", "generate", "verify", "safety", "total"} <= set(trace["latency_ms"])
    assert trace["queries_en"] == ["motor overheating", "reducer overheating"]
    assert trace["hits"][0]["id"] == KB_ID and trace["top_score"] == 0.9 and trace["threshold"] == 0.55
    assert trace["schema_retries"] == 0 and trace["error"] is None


def test_nameplate_equipment_goes_to_search_and_prompt(make):
    retriever = FakeRetriever([ts_hit()])
    pipeline = make(FakeLLM("유형: troubleshooting\n설비: 없음\n부품: 모터\n증상: 과열", QUERIES, ts_answer()), retriever)
    equipment = {"model_name": "138-ACC", "aliases": ["ACC"], "doc_ids": ["hytrol_138acc"]}
    out = pipeline.answer("모터가 뜨거워요", {**state(), "equipment": equipment}, "text")
    assert retriever.calls[0]["equipment"] == equipment                       # 명판 설비로 필터
    assert pipeline.prompts[-1]["equipment"] == "138-ACC"
    assert out["equipment"] == {"model": "138-ACC", "doc_ids": ["hytrol_138acc"], "source": "nameplate"}


# ------------------------------------------------------------------------------ 확인되지 않음
def test_out_of_scope_skips_search(make, db):
    retriever = FakeRetriever([ts_hit()])
    out = make(FakeLLM(UNDERSTAND_OUT), retriever).answer("점심 메뉴 추천해 줘", state(), "text")
    assert out["status"] == "not_found" and out["message"] == MSG_OUT_OF_SCOPE
    assert retriever.calls == [] and out["safety"] == []
    assert logs(db)[0]["not_found"] == 0                       # 범위 밖은 "답 못 찾은 질문"에서 빠짐


def test_low_score_does_not_generate(make, db):
    llm = FakeLLM(UNDERSTAND_TS, QUERIES)                      # 생성용 답을 준비하지 않음 → 부르면 오류
    out = make(llm, FakeRetriever([ts_hit(score=0.3)])).answer("모터 과열", state(), "text")
    assert out["status"] == "not_found" and out["message"] == MSG_NOT_FOUND
    assert len(llm.calls) == 2 and out["trace"]["top_score"] == 0.3
    assert logs(db)[0]["not_found"] == 1


def test_llm_says_not_found_without_retry(make):
    llm = FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer(not_found=True))
    out = make(llm, FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "not_found" and len(llm.calls) == 3 and out["trace"]["schema_retries"] == 0


def test_all_causes_invented_becomes_not_found(make):
    llm = FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer(cause_ids=[f"{KB_ID}#c9"]))
    out = make(llm, FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "not_found" and out["trace"]["removed_cause_ids"] == [f"{KB_ID}#c9"]


def test_changed_action_count_is_removed(make):
    """조치를 KB 보다 많게 옮긴 원인은 validator 가 지움 → 남는 원인이 없으면 not_found"""
    padded = {**KO_CAUSES[f"{KB_ID}#c1"], "actions": ["부하를 줄이세요.", "모터를 교체하세요."]}   # KB 는 조치 1개
    answer = {**ts_answer(), "causes": [padded]}
    out = make(FakeLLM(UNDERSTAND_TS, QUERIES, answer), FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "not_found" and out["trace"]["removed_cause_ids"] == [f"{KB_ID}#c1"]


# ------------------------------------------------------------------------------ 재요청 · 검증 기록
def test_broken_format_retried_once(make):
    llm = FakeLLM(UNDERSTAND_TS, QUERIES, None, ts_answer())  # 첫 답은 형식이 깨짐 → 다시 요청
    out = make(llm, FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "answered" and out["trace"]["schema_retries"] == 1


def test_not_korean_twice_becomes_not_found(make):
    english = {**ts_answer(), "summary": "Check the motor load and voltage first."}
    llm = FakeLLM(UNDERSTAND_TS, QUERIES, english, english)
    out = make(llm, FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "not_found" and out["trace"]["schema_retries"] == 1


def test_invented_cause_removed_and_quote_replaced(make):
    answer = ts_answer(cause_ids=[f"{KB_ID}#c1", f"{KB_ID}#c9"], quote="The motor is too hot.")
    out = make(FakeLLM(UNDERSTAND_TS, QUERIES, answer), FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert [c["id"] for c in out["causes"]] == [f"{KB_ID}#c1"]
    assert out["trace"]["removed_cause_ids"] == [f"{KB_ID}#c9"]
    assert out["trace"]["replaced_quote_refs"] == [KB_ID]
    assert out["sources"][0]["quote"] in ts_hit().text                         # 원문 문장으로 바뀜


# ------------------------------------------------------------------------------ 오류 · 로그
def test_search_failure_becomes_error(make, db):
    retriever = FakeRetriever([], error=ConnectionError("chroma down"))
    out = make(FakeLLM(UNDERSTAND_TS, QUERIES), retriever).answer("모터 과열", state(), "text")
    assert out["status"] == "error" and out["message"] == MSG_ERROR
    assert out["trace"]["error"]["stage"] == "search" and out["trace"]["error"]["code"] == "ConnectionError"
    assert logs(db)[0]["error"].startswith("search/ConnectionError")


def test_generate_failure_becomes_error(make):
    llm = FakeLLM(UNDERSTAND_TS, QUERIES, TimeoutError("llm timeout"))
    out = make(llm, FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "error" and out["trace"]["error"]["stage"] == "generate"


def test_log_saved_with_user_and_source(make, db):
    make(FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer()), FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    row = logs(db)[0]
    assert row["source"] == "user" and row["employee_id"] == "e1" and row["question"] == "모터 과열"
    assert row["not_found"] == 0 and row["safety_included"] == 1


def test_eval_pipeline_logs_as_eval(make, db):
    pipeline = make(FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer()), FakeRetriever([ts_hit()]), log_source="eval")
    pipeline.answer("모터 과열", {}, "text")
    assert logs(db)[0]["source"] == "eval" and logs(db)[0]["employee_id"] is None


def test_log_failure_keeps_answer(make, db, capsys):
    db.broken = True
    out = make(FakeLLM(UNDERSTAND_TS, QUERIES, ts_answer()), FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "answered"
    assert "e1" not in capsys.readouterr().out                                  # 사원번호는 출력하지 않음


# ------------------------------------------------------------------------------ load_kb
def test_load_kb_reads_all_files(kb):
    assert len(kb) == len(list((ROOT / "kb").glob("*/*.json")))
    assert kb[KB_ID]["causes"][0]["cause_id"] == f"{KB_ID}#c1"


def test_load_kb_rejects_duplicate_id(tmp_path):
    source = (ROOT / "kb" / "troubleshooting" / f"{KB_ID}.json").read_text(encoding="utf-8")
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / f"{KB_ID}.json").write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="겹칩니다"):
        load_kb(tmp_path)

def test_retry_tells_llm_what_to_fix(make):
    """한국어 검사에 떨어지면 같은 요청을 반복하지 않고, 첫 답과 고칠 점을 붙여 다시 묻는다 (temperature 0 에서 같은 답 반복 방지)"""
    english_cause = {**ts_answer(), "causes": [{**KO_CAUSES[f"{KB_ID}#c1"], "cause": "Conveyor is overloaded."}]}
    seen = []

    class RecordingLLM(FakeLLM):
        def chat(self, messages, model=None, json_schema=None):
            seen.append(messages)
            return super().chat(messages, model, json_schema)

    llm = RecordingLLM(UNDERSTAND_TS, QUERIES, english_cause, ts_answer())
    out = make(llm, FakeRetriever([ts_hit()])).answer("모터 과열", state(), "text")
    assert out["status"] == "answered" and out["trace"]["schema_retries"] == 1
    first, second = seen[2], seen[3]
    assert second[:len(first)] == first                              # 처음 요청은 그대로 두고
    assert second[-2]["role"] == "assistant" and second[-1]["role"] == "user"   # 첫 답 + 고칠 점을 덧붙임
    assert "한국어" in second[-1]["content"]
