"""
validator.py 테스트 (AnswerValidator 근거 검증)

- 실제 OpenAI · DB · .env 를 쓰지 않는다. 가짜 LLM 답(dict)과 가짜 검색 결과(Hit)로 검사한다
- KB 의 [자료] 글은 prompts.KB_TROUBLESHOOTING 틀로 만든다 (build_prompt 가 LLM 에게 보여 주는 글과 같게)
실행: python -m pytest tests/validator_test.py -v
"""
import copy

import pytest

from chatbot.core.schemas import Hit
from chatbot.rag.prompts import KB_CAUSE, KB_TROUBLESHOOTING
from chatbot.rag.validator import MAX_QUOTE_CHARS, AnswerValidator

KB_ID = "TS-HYTROL138ACC-T01-01"
KB_TEXT = KB_TROUBLESHOOTING.format(
    applies_to="138-ACC", symptom="Motor overheats", alarm_codes="",
    causes="\n".join([
        KB_CAUSE.format(cause_id=f"{KB_ID}#c1", cause="Conveyor overloaded.", actions="Reduce load → Check amperage"),
        KB_CAUSE.format(cause_id=f"{KB_ID}#c2", cause="Low supply voltage.", actions="Check supply voltage"),
    ]),
)
CHUNK_TEXT = ("Lubricate the drive chain every 2,080 hours with ISO VG 220 oil.\n"
              "Check the belt tension monthly. Replace worn sprockets.")


def kb_hit():
    return Hit(id=KB_ID, text=KB_TEXT, score=0.9, doc_id="hytrol_138acc", page=22,
               metadata={"doc_id": "hytrol_138acc", "page": 22, "chunk_type": "kb", "kb_type": "troubleshooting"})


def chunk_hit():
    return Hit(id="hytrol-p30-c2", text=CHUNK_TEXT, score=0.6, doc_id="hytrol_138acc", page=30,
               metadata={"doc_id": "hytrol_138acc", "page": 30, "chunk_type": "text"})


def llm_answer(**changes):
    """규칙대로 만든 정상 LLM 답. 필요한 칸만 바꿔 쓴다."""
    answer = {
        "not_found": False,
        "summary": "모터 과열은 컨베이어 과부하가 가장 먼저 확인할 원인입니다.",
        "cause_ids": [f"{KB_ID}#c1"],
        "actions": [],
        "sources": [{"ref_id": KB_ID, "quote": "Conveyor overloaded."}],
    }
    answer.update(changes)
    return answer


@pytest.fixture
def validator():
    return AnswerValidator()


# ------------------------------------------------------------------------------
# validate_schema
# ------------------------------------------------------------------------------
def test_schema_ok(validator):
    assert validator.validate_schema(llm_answer())


@pytest.mark.parametrize("broken", [
    None,                                                   # generate 에서 형식이 깨진 경우
    {"summary": "요약"},                                    # 칸이 빠짐
    {**llm_answer(), "follow_ups": []},                     # 정의에 없는 칸
    llm_answer(cause_ids="TS-A-T01-01#c1"),                 # 목록이 아님
    llm_answer(actions=[{"text": "점검"}]),                 # ref_id 빠짐
])
def test_schema_broken(validator, broken):
    assert not validator.validate_schema(broken)


# ------------------------------------------------------------------------------
# check_language
# ------------------------------------------------------------------------------
def test_korean_with_english_terms_passes(validator):
    answer = llm_answer(actions=[{"text": "구동 체인(drive chain)에 ISO VG 220 오일을 바르세요.", "ref_id": "x"}])
    assert validator.check_language(answer)


def test_not_found_sentence_passes(validator):
    assert validator.check_language(llm_answer(not_found=True, summary="자료에서 확인되지 않습니다.", cause_ids=[], sources=[]))


@pytest.mark.parametrize("answer", [
    llm_answer(summary="The conveyor is overloaded. Reduce the load."),
    llm_answer(actions=[{"text": "Reduce the load on the conveyor.", "ref_id": "x"}]),   # 단계 하나라도 영어면 실패
    llm_answer(summary="   "),
])
def test_not_korean_fails(validator, answer):
    assert not validator.check_language(answer)


def test_english_quote_is_not_checked(validator):
    answer = llm_answer(sources=[{"ref_id": KB_ID, "quote": "Conveyor overloaded and motor runs hot."}])
    assert validator.check_language(answer)


# ------------------------------------------------------------------------------
# verify_causes
# ------------------------------------------------------------------------------
def test_causes_keep_only_ids_in_kb_text(validator):
    answer = llm_answer(cause_ids=[
        f"{KB_ID}#c2",
        f"{KB_ID}#c9",                    # KB 글에 없는 순번 → 지어낸 id
        "TS-OTHER-T01-01#c1",             # 검색되지 않은 KB
        "hytrol_138acc_ts_04#c1",         # 옛 형식
        f"{KB_ID}#c2",                    # 중복
    ])
    checked = validator.verify_causes(answer, [kb_hit(), chunk_hit()])
    assert checked["cause_ids"] == [f"{KB_ID}#c2"]


def test_cause_id_prefix_does_not_match_longer_number(validator):
    hit = kb_hit()
    hit.text = hit.text.replace(f"{KB_ID}#c1:", f"{KB_ID}#c10:")
    checked = validator.verify_causes(llm_answer(cause_ids=[f"{KB_ID}#c1"]), [hit])
    assert checked["cause_ids"] == []     # "#c1" 이 "#c10" 안에서 찾아지면 안 된다


def test_causes_need_kb_hit(validator):
    # 같은 id 가 본문 청크에 적혀 있어도 KB 가 아니면 인정하지 않는다
    chunk = chunk_hit()
    chunk.text += f"\n{KB_ID}#c1"
    assert validator.verify_causes(llm_answer(), [chunk])["cause_ids"] == []


def test_verify_causes_does_not_change_input(validator):
    answer = llm_answer(cause_ids=["TS-OTHER-T01-01#c1"])
    before = copy.deepcopy(answer)
    validator.verify_causes(answer, [kb_hit()])
    assert answer == before


# ------------------------------------------------------------------------------
# verify_quote
# ------------------------------------------------------------------------------
def test_quote_in_text_is_kept(validator):
    answer = llm_answer(sources=[{"ref_id": "hytrol-p30-c2", "quote": "Check the belt tension monthly."}])
    checked = validator.verify_quote(answer, [kb_hit(), chunk_hit()])
    assert checked["sources"] == answer["sources"]


def test_quote_ignores_line_break_difference(validator):
    quote = "Lubricate the drive chain every 2,080 hours with ISO VG 220 oil. Check the belt tension monthly."
    answer = llm_answer(sources=[{"ref_id": "hytrol-p30-c2", "quote": quote}])
    assert validator.verify_quote(answer, [chunk_hit()])["sources"][0]["quote"] == quote


def test_wrong_quote_replaced_with_closest_sentence(validator):
    answer = llm_answer(sources=[{"ref_id": "hytrol-p30-c2", "quote": "Belt tension should be checked every month"}])
    quote = validator.verify_quote(answer, [chunk_hit()])["sources"][0]["quote"]
    assert quote == "Check the belt tension monthly."
    assert quote in CHUNK_TEXT                       # 바꾼 인용문은 항상 원문의 부분 문자열


def test_kb_quote_replaced_without_korean_label(validator):
    answer = llm_answer(sources=[{"ref_id": KB_ID, "quote": "The supply voltage is too low"}])
    quote = validator.verify_quote(answer, [kb_hit()])["sources"][0]["quote"]
    assert quote == "Low supply voltage."            # "- 원인 id …:" 이름표는 빼고 원문만


def test_unknown_source_and_its_action_removed(validator):
    answer = llm_answer(
        cause_ids=[],
        actions=[{"text": "체인을 윤활하세요.", "ref_id": "hytrol-p30-c2"},
                 {"text": "모터를 교체하세요.", "ref_id": "made-up-id"}],
        sources=[{"ref_id": "hytrol-p30-c2", "quote": "Replace worn sprockets."},
                 {"ref_id": "made-up-id", "quote": "Replace the motor."}],
    )
    checked = validator.verify_quote(answer, [chunk_hit()])
    assert [s["ref_id"] for s in checked["sources"]] == ["hytrol-p30-c2"]
    assert [a["ref_id"] for a in checked["actions"]] == ["hytrol-p30-c2"]


def test_long_replacement_is_cut(validator):
    hit = chunk_hit()
    hit.text = "word " * 200
    answer = llm_answer(sources=[{"ref_id": hit.id, "quote": "not in text"}])
    quote = validator.verify_quote(answer, [hit])["sources"][0]["quote"]
    assert len(quote) <= MAX_QUOTE_CHARS and quote in hit.text


def test_checked_answer_still_matches_schema(validator):
    answer = llm_answer(cause_ids=[f"{KB_ID}#c1", "TS-OTHER-T01-01#c1"],
                        sources=[{"ref_id": KB_ID, "quote": "overloaded conveyor"}])
    hits = [kb_hit(), chunk_hit()]
    checked = validator.verify_quote(validator.verify_causes(answer, hits), hits)
    assert validator.validate_schema(checked)
