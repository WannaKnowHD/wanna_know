"""
gradio_app.py ChatApp.on_image 테스트 (WBS 3.7.5 · 기능 I-01·I-02·I-03)

- 사진 판별·명판 판독·손상 분석·파이프라인은 가짜 객체로 바꿔 끼워서 API 를 부르지 않는다
- 명판 매칭(match_model)은 실제 NameplateReader 코드와 저장소의 data/models.csv 를 쓴다
실행: python -m pytest tests/on_image_test.py -v
"""
import os

import pytest

from chatbot.core.schemas import AnswerResponse, User
from chatbot.core.state import ConversationState
from chatbot.rag.glossary import Glossary
from chatbot.ui.gradio_app import (MSG_DAMAGE_RETAKE, MSG_NAMEPLATE_RETAKE, MSG_PHOTO_ERROR, MSG_PHOTO_OTHER, PHOTO_QUESTIONS,
                                    ChatApp)
from chatbot.vision.vision import NameplateReader

MODELS = Glossary().load_models(os.path.join("data", "models.csv"))
USER = User("e1", "엔지니어", "user")


class FakeRouter:
    def __init__(self, kind, confidence=0.95):
        self.result = (kind, confidence)

    def classify_image(self, image):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeReader(NameplateReader):
    """판독(read_nameplate)만 가짜. 정리·매칭은 실제 코드"""

    def __init__(self, read):
        super().__init__(llm_client=None)
        self.read = read

    def read_nameplate(self, image):
        return {"serial": "978747", "maker": "Hytrol", "confidence": 0.95, "retake": False, **self.read}


class FakeAnalyzer:
    def __init__(self, result):
        self.result = result

    def describe_damage(self, image):
        return {"label": "edge_damage", "symptom_ko": "벨트 가장자리가 닳고 찢어졌어요", "confidence": 0.9,
                "retake": False, **self.result}


class FakePipeline:
    def __init__(self, answer=None):
        self.answer_value = answer
        self.calls = []

    def answer(self, question, state, input_type):
        self.calls.append((question, input_type))
        self.equipment = (state.get("equipment") or {}).get("model_name")   # 답할 때 어떤 설비 기준이었는지
        if isinstance(self.answer_value, Exception):
            raise self.answer_value
        if self.answer_value is None:
            raise NotImplementedError("RagPipeline.answer")
        return self.answer_value


class RecordingState(ConversationState):
    """save_history 로 저장한 대화를 기억해 두는 대화 상태 (DB 없음)"""

    def __init__(self):
        super().__init__()
        self.turns = []

    def save_history(self, state, turn):
        self.turns.append(turn)
        return super().save_history(state, turn)


def make_app(kind="nameplate", read=None, damage=None, pipeline=None, confidence=0.95):
    conversation = RecordingState()
    pipeline = pipeline or FakePipeline()
    # 앱의 질문 답변(_answer)과 손상 사진 검색이 같은 파이프라인을 쓰게 (실제 앱도 같은 객체)
    app = ChatApp(conversation=conversation, pipeline=pipeline)
    app._vision_parts = {
        "router": FakeRouter(kind, confidence), "reader": FakeReader(read or {"raw_text": "138-ACC"}),
        "analyzer": FakeAnalyzer(damage or {}), "models": MODELS, "pipeline": pipeline,
    }
    return app, conversation


def run(app, state=None, question=""):
    state = {"user": USER, "admin_mode": False, "equipment": None} if state is None else state
    return app.on_image(b"img", [{"role": "user", "content": "이전 대화"}], state, question)


def last_card(history):
    return history[-1]["content"]


# ------------------------------------------------------------------------------
# 사진 판별 (I-01)
# ------------------------------------------------------------------------------
def test_other_photo_retake():
    app, conversation = make_app("other", confidence=0.99)
    history, state = run(app)
    assert len(history) == 2 and history[-1]["role"] == "assistant"   # 이전 대화 뒤에 답변 카드 1개
    assert MSG_PHOTO_OTHER in last_card(history)
    assert conversation.turns[0]["answer"]["status"] == "retake"
    assert state["equipment"] is None


def test_low_confidence_is_only_recorded():
    """확신도는 판단에 쓰지 않음: 판별 확신도가 낮아도 명판으로 처리하고 값은 응답에 남긴다"""
    app, conversation = make_app("nameplate", read={"raw_text": "138 ACC"}, confidence=0.3)
    _, state = run(app)
    answer = conversation.turns[0]["answer"]
    assert answer["status"] == "nameplate" and state["equipment"]["model_name"] == "138-ACC"
    assert answer["image"]["confidence"] == 0.3


def test_not_logged_in_or_empty_image():
    app, _ = make_app()
    assert app.on_image(b"img", [], {"user": None}) == ([], {"user": None})
    assert app.on_image(b"", [], {"user": USER}) == ([], {"user": USER})


def test_vision_error_shows_notice():
    app, conversation = make_app()
    app._vision_parts["router"].result = RuntimeError("API down")
    history, _ = run(app)
    assert MSG_PHOTO_ERROR in last_card(history)
    assert conversation.turns[0]["answer"]["status"] == "error"


# ------------------------------------------------------------------------------
# 명판 (I-02)
# ------------------------------------------------------------------------------
def test_nameplate_exact_sets_equipment():
    app, conversation = make_app(read={"raw_text": "138 ACC"})   # 표기가 달라도 같은 모델
    history, state = run(app)
    assert state["equipment"] == {"model_name": "138-ACC", "aliases": ["ACC"], "doc_ids": ["hytrol_138acc"]}
    assert "명판에서 모델명 138-ACC을(를) 확인했습니다" in last_card(history)   # 카드 제목 (Figma 04)
    assert "이제부터 138-ACC 매뉴얼 기준으로 답변합니다" in last_card(history)
    saved = conversation.turns[0]
    assert saved["question"] == "(사진) 명판"
    assert saved["answer"]["status"] == "nameplate"
    assert saved["answer"]["equipment"] == {"model": "138-ACC", "doc_ids": ["hytrol_138acc"], "source": "nameplate"}
    assert saved["answer"]["image"]["nameplate"]["match"] == "exact"


def test_nameplate_near_keeps_equipment():
    app, conversation = make_app(read={"raw_text": "138-AC"})   # 한 글자 차이, 후보 하나
    _, state = run(app)
    assert state["equipment"] is None                           # 확인 없이 설비를 바꾸지 않음
    answer = conversation.turns[0]["answer"]
    assert answer["image"]["nameplate"]["match"] == "near"
    assert "질문에 모델명을 넣어" in answer["message"] and "138-ACC" in answer["message"]


def test_nameplate_none_keeps_current_equipment():
    app, conversation = make_app(read={"raw_text": "MB-200-400-WD"})
    before = {"user": USER, "admin_mode": False, "equipment": {"model_name": "L400", "aliases": [], "doc_ids": ["preco_l_series"]}}
    _, state = run(app, before)
    assert state["equipment"]["model_name"] == "L400"
    message = conversation.turns[0]["answer"]["message"]
    assert "목록에 없습니다" in message and "L400 기준" in message


@pytest.mark.parametrize("read", [
    {"raw_text": "L800"},                               # L400·L500 둘 다 한 글자 차이 → 잘못 읽었을 가능성
    {"raw_text": None, "confidence": 0.0, "retake": True},   # 판독 실패
])
def test_nameplate_retake(read):
    app, conversation = make_app(read=read)
    history, state = run(app)
    assert MSG_NAMEPLATE_RETAKE in last_card(history)
    assert conversation.turns[0]["answer"]["status"] == "retake" and state["equipment"] is None


# ------------------------------------------------------------------------------
# 손상 사진 (I-03)
# ------------------------------------------------------------------------------
def answered():
    return {
        "status": "answered", "input_type": "damage_photo", "question_type": "troubleshooting",
        "summary": "가장자리 마모는 하중 쏠림이 주원인입니다.",
        "causes": [{"id": "TS-FENNERBELT-T01-18#c1", "cause": "Off-center loading or poor loading",
                    "actions": ["adjust chute to place load on center of belt"]}],
        "sources": [{"ref_id": "TS-FENNERBELT-T01-18", "quote": "Excessive edge wear, broken edges.",
                     "doc_id": "fenner_belt", "doc_title": "Fenner", "page": 27, "chunk_type": "kb"}],
    }


def test_damage_answer_from_pipeline():
    pipeline = FakePipeline(answered())
    app, conversation = make_app("belt_damage", pipeline=pipeline)
    history, _ = run(app)
    assert pipeline.calls == [("벨트 가장자리가 닳고 찢어졌어요", "damage_photo")]   # 증상 문장으로 검색
    saved = conversation.turns[0]
    assert saved["question"] == "(사진) 손상 부위"
    assert saved["answer"]["status"] == "answered"
    assert saved["answer"]["image"]["damage"]["label"] == "edge_damage"           # 분석 결과도 함께 남김
    assert AnswerResponse.model_validate(saved["answer"])                         # 저장한 값이 응답 스키마에 맞음


def test_damage_before_pipeline_ready():
    app, conversation = make_app("belt_damage")   # FakePipeline() = NotImplementedError
    history, _ = run(app)
    assert "답변 기능 준비 중" in last_card(history)
    assert conversation.turns[0]["answer"]["image"]["damage"]["symptom_ko"] == "벨트 가장자리가 닳고 찢어졌어요"


def test_damage_retake():
    pipeline = FakePipeline(answered())
    app, conversation = make_app("belt_damage", damage={"label": "other", "symptom_ko": "", "retake": True},
                                 pipeline=pipeline)
    history, _ = run(app)
    assert MSG_DAMAGE_RETAKE in last_card(history)
    assert pipeline.calls == []                   # 재촬영이면 검색·답변을 만들지 않음


# ------------------------------------------------------------------------------
# 사진 + 질문을 함께 보냄 → 둘 다 반영한 답변 카드 1개
# ------------------------------------------------------------------------------
QUESTION = "벨트가 미끄러져요"


def test_nameplate_with_question_answers_with_that_equipment():
    pipeline = FakePipeline(answered())
    app, conversation = make_app(read={"raw_text": "138 ACC"}, pipeline=pipeline)
    history, state = run(app, question=QUESTION)
    assert pipeline.calls == [(QUESTION, "nameplate_photo")]
    assert pipeline.equipment == "138-ACC"                        # 명판으로 설비를 정한 뒤 그 기준으로 답함
    assert len(history) == 2                                      # 이전 대화 + 카드 1개
    card = last_card(history)
    assert "138-ACC 설비를 확인했습니다" in card and "가장자리 마모는" in card   # 사진 결과 줄 + 답변
    saved = conversation.turns[0]
    assert len(conversation.turns) == 1 and saved["question"] == f"(사진) 명판 {QUESTION}"
    assert saved["answer"]["status"] == "answered" and saved["answer"]["input_type"] == "nameplate_photo"
    assert saved["answer"]["image"]["nameplate"]["match"] == "exact"


def test_damage_with_question_searches_both_once():
    pipeline = FakePipeline(answered())
    app, conversation = make_app("belt_damage", pipeline=pipeline)
    run(app, question="이거 왜 이래요?")
    assert pipeline.calls == [("이거 왜 이래요? (사진: 벨트 가장자리가 닳고 찢어졌어요)", "damage_photo")]
    saved = conversation.turns[0]
    assert saved["question"] == "(사진) 손상 부위 이거 왜 이래요?" and saved["answer"]["message"] is None


@pytest.mark.parametrize("kind, read, damage, note", [
    ("other", None, None, MSG_PHOTO_OTHER),                                          # 그 외 사진
    ("nameplate", {"raw_text": None, "confidence": 0.0, "retake": True}, None, MSG_NAMEPLATE_RETAKE),
    ("belt_damage", None, {"label": "other", "symptom_ko": "", "retake": True}, MSG_DAMAGE_RETAKE),
])
def test_photo_failed_still_answers_question(kind, read, damage, note):
    """사진 처리가 안 돼도 입력한 질문은 답하고, 사진 안내는 같은 카드 맨 위에"""
    pipeline = FakePipeline(answered())
    app, conversation = make_app(kind, read=read, damage=damage, pipeline=pipeline)
    history, state = run(app, question=QUESTION)
    assert [q for q, _ in pipeline.calls] == [QUESTION] and state["equipment"] is None
    answer = conversation.turns[0]["answer"]
    assert answer["status"] == "answered" and answer["message"] == note
    assert note in last_card(history)


def test_vision_error_still_answers_question():
    pipeline = FakePipeline(answered())
    app, conversation = make_app(pipeline=pipeline)
    app._vision_parts["router"].result = RuntimeError("API down")
    history, _ = run(app, question=QUESTION)
    assert pipeline.calls == [(QUESTION, "other_photo")]
    assert conversation.turns[0]["answer"]["message"] == MSG_PHOTO_ERROR


def test_question_not_found_joins_both_notices():
    """질문 답이 확인되지 않음이면 사진 결과와 안내 문구를 이어서 안내 카드 1개로"""
    not_found = {"status": "not_found", "input_type": "text", "message": "자료에서 확인되지 않습니다."}
    app, conversation = make_app(read={"raw_text": "MB-200-400-WD"}, pipeline=FakePipeline(not_found))
    history, _ = run(app, question=QUESTION)
    message = conversation.turns[0]["answer"]["message"]
    assert "목록에 없습니다" in message and message.endswith("자료에서 확인되지 않습니다.")
    assert conversation.turns[0]["answer"]["status"] == "not_found"


# ------------------------------------------------------------------------------
# 요청 로그 (TS-L-01): 파이프라인을 거치지 않은 사진도 chat_logs 에 1행
# ------------------------------------------------------------------------------
class FakeRequestLogger:
    def __init__(self, fail=False):
        self.records, self.fail = [], fail

    def build_record(self, answer, question, employee_id, source="user"):
        return {"answer": answer, "question": question, "employee_id": employee_id, "source": source}

    def log_request(self, record):
        if self.fail:
            raise RuntimeError("DB down")
        self.records.append(record)


def logged_app(kind="nameplate", **kw):
    app, conversation = make_app(kind, **kw)
    app._pipeline.request_logger = FakeRequestLogger()
    app._pipeline.log_source = "user"
    return app, conversation, app._pipeline.request_logger


@pytest.mark.parametrize("kind, read, damage, input_type", [
    ("nameplate", {"raw_text": "138 ACC"}, None, "nameplate_photo"),                        # 설비 설정
    ("other", None, None, "other_photo"),                                                   # 그 외 사진
    ("belt_damage", None, {"label": "other", "symptom_ko": "", "retake": True}, "damage_photo"),   # 손상 재촬영
])
def test_photo_only_is_logged_once(kind, read, damage, input_type):
    app, _, request_logger = logged_app(kind, read=read, damage=damage)
    run(app)
    (record,) = request_logger.records
    assert record["employee_id"] == "e1" and record["source"] == "user"
    assert record["answer"]["input_type"] == input_type
    assert record["question"] == PHOTO_QUESTIONS[input_type]
    assert record["answer"]["trace"]["latency_ms"]["total"] >= 0          # 걸린 시간도 남김


@pytest.mark.parametrize("kind, question", [("belt_damage", ""), ("nameplate", QUESTION)])
def test_pipeline_path_is_not_logged_twice(kind, question):
    """손상 사진 검색·사진+질문은 RagPipeline.answer 가 로그를 남기므로 화면에서 또 남기지 않음"""
    app, _, request_logger = logged_app(kind, pipeline=FakePipeline(answered()))
    run(app, question=question)
    assert request_logger.records == []


def test_photo_log_failure_keeps_answer():
    app, conversation, request_logger = logged_app()
    request_logger.fail = True
    history, state = run(app)
    assert state["equipment"]["model_name"] == "138-ACC" and conversation.turns[0]["answer"]["status"] == "nameplate"


def test_without_question_keeps_photo_only_behavior():
    pipeline = FakePipeline(answered())
    app, conversation = make_app(read={"raw_text": "138 ACC"}, pipeline=pipeline)
    run(app, question="   ")
    assert pipeline.calls == []                                  # 빈 질문이면 명판 결과 카드만
    assert conversation.turns[0]["question"] == "(사진) 명판"
