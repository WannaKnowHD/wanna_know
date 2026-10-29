"""
pipeline.py 답변 생성 테스트 (RagPipeline.build_prompt · generate)

- 실제 OpenAI · DB · .env 를 쓰지 않는다. 가짜 LLM 과 가짜 검색 결과(Hit)로 검사한다
- Hit.metadata 는 retriever.py 머리말의 "색인 약속"과 같은 키를 쓴다
실행: python -m pytest tests/pipeline_prompt_test.py -v
"""
import pytest

from chatbot.core.schemas import Hit, LLMAnswer
from chatbot.pipeline import MAX_CONTEXT_TOKENS, RagPipeline, _count_tokens
from chatbot.rag.prompts import ANSWER_SYSTEM, NO_CONTEXT, NO_EQUIPMENT, NO_QUESTION_TYPE


class FakeLLM:
    """chat 호출을 기록하고 정해 둔 결과를 돌려주는 가짜 LLMClient."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"text": "{}", "parsed": None, "model": "fake", "latency_ms": 1}

    def chat(self, messages, model=None, json_schema=None):
        self.calls.append({"messages": messages, "json_schema": json_schema})
        return self.result


def kb_hit(kb_id, kb_type="troubleshooting", text="적용 설비: 138-ACC", page=22):
    return Hit(id=kb_id, text=text, score=0.9, doc_id="hytrol_138acc", page=page,
               metadata={"doc_id": "hytrol_138acc", "page": page, "chunk_type": "kb", "kb_type": kb_type,
                         "all_models": False})


def chunk_hit(chunk_id, chunk_type="text", text="Check motor amperage.", page=23):
    return Hit(id=chunk_id, text=text, score=0.5, doc_id="hytrol_138acc", page=page,
               metadata={"doc_id": "hytrol_138acc", "page": page, "chunk_type": chunk_type, "all_models": False})


def context_of(messages):
    """user 메시지에서 [자료] 부분만 꺼낸다."""
    user = messages[1]["content"]
    return user.split("[자료]\n", 1)[1].split("\n\n[질문 유형]", 1)[0]


@pytest.fixture
def pipeline():
    return RagPipeline(FakeLLM())


# ------------------------------------------------------------------------------
# build_prompt
# ------------------------------------------------------------------------------
def test_messages_are_system_and_user(pipeline):
    messages = pipeline.build_prompt("모터가 뜨거워요", [kb_hit("TS-HYTROL138ACC-T01-01")])
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == ANSWER_SYSTEM
    assert messages[1]["content"].endswith("질문: 모터가 뜨거워요")


def test_context_keeps_order_and_kind_labels(pipeline):
    hits = [
        kb_hit("TS-HYTROL138ACC-T01-01"),
        kb_hit("MT-HYTROL138ACC-T02-01", kb_type="maintenance"),
        chunk_hit("hytrol-p23-c1"),
        chunk_hit("hytrol-p24-t1", chunk_type="table"),
    ]
    context = context_of(pipeline.build_prompt("q", hits))
    assert "--- id: TS-HYTROL138ACC-T01-01 | 문서: hytrol_138acc p.22 | 종류: 고장 KB" in context
    assert "종류: 점검 KB" in context and "종류: 본문" in context and "종류: 표" in context
    positions = [context.index(f"id: {hit.id} ") for hit in hits]
    assert positions == sorted(positions)          # 검색 순위 그대로


def test_question_type_and_equipment(pipeline):
    user = pipeline.build_prompt("q", [kb_hit("TS-A-T01-01")], "troubleshooting", " 138-ACC ")[1]["content"]
    assert "[질문 유형] troubleshooting" in user
    assert "[설비] 138-ACC" in user


def test_missing_type_and_equipment_use_defaults(pipeline):
    user = pipeline.build_prompt("q", [kb_hit("TS-A-T01-01")])[1]["content"]
    assert f"[질문 유형] {NO_QUESTION_TYPE}" in user
    assert f"[설비] {NO_EQUIPMENT}" in user


def test_no_hits_uses_no_context(pipeline):
    assert context_of(pipeline.build_prompt("q", [])) == NO_CONTEXT


def test_stops_before_token_limit(pipeline):
    long_text = "belt " * 1500                     # 자료 1건이 약 1,500토큰
    hits = [chunk_hit(f"c{i}", text=long_text) for i in range(5)]
    context = context_of(pipeline.build_prompt("q", hits))
    assert _count_tokens(context) <= MAX_CONTEXT_TOKENS
    assert "id: c0 " in context and "id: c1 " in context
    assert "id: c2 " not in context                # 3건째에서 4,000토큰을 넘으므로 멈춘다


def test_first_hit_too_long_is_cut(pipeline):
    hits = [chunk_hit("big", text="belt " * 6000), chunk_hit("small")]
    context = context_of(pipeline.build_prompt("q", hits))
    assert context.startswith("--- id: big ")      # 자료가 비지 않게 1위 자료를 잘라 넣는다
    assert _count_tokens(context) <= MAX_CONTEXT_TOKENS
    assert "id: small " not in context


# ------------------------------------------------------------------------------
# generate
# ------------------------------------------------------------------------------
def test_generate_asks_llm_answer_schema():
    llm = FakeLLM()
    pipeline = RagPipeline(llm)
    messages = pipeline.build_prompt("q", [kb_hit("TS-A-T01-01")])
    pipeline.generate(messages)
    assert llm.calls[0]["json_schema"] is LLMAnswer
    assert llm.calls[0]["messages"] is messages


def test_generate_returns_chat_result_as_is():
    parsed = {"not_found": False, "summary": "요약",
              "causes": [{"id": "TS-A-T01-01#c1", "cause": "과부하", "actions": ["부하를 줄이세요."]}], "actions": [],
              "sources": [{"ref_id": "TS-A-T01-01", "quote": "Conveyor overloaded"}]}
    result = {"text": "{...}", "parsed": parsed, "model": "fake", "latency_ms": 5}
    assert RagPipeline(FakeLLM(result)).generate([]) == result


def test_generate_keeps_none_when_format_broken():
    result = {"text": "not json", "parsed": None, "model": "fake", "latency_ms": 5}
    assert RagPipeline(FakeLLM(result)).generate([])["parsed"] is None
