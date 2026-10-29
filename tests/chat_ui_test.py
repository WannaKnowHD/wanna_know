"""채팅 화면 틀·가짜 응답 확인 (WBS 3.7.1, 로컬 전용). DB·모델 없이 메모리 가짜 DB 로 검사함.

실행 (프로젝트 루트에서):
    pytest tests/chat_ui_test.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # login_ui_test.FakeDb (users·sessions·chat_history 가짜 DB)

import pytest

from chatbot.core.auth import AuthManager
from chatbot.core.schemas import Model
from chatbot.core.state import ConversationState
from chatbot.ui.chat import (
    CHAT_CSS_PATHS,
    EXAMPLE_QUESTIONS,
    fake_answer,
    fake_photo_answer,
    render_answer_card,
    render_equipment,
    render_user_message,
    render_welcome,
)
from chatbot.ui.gradio_app import ChatApp
from login_ui_test import FakeDb


@pytest.fixture
def env():
    db = FakeDb()
    conversation = ConversationState(db)
    app = ChatApp(auth=AuthManager(db, session_hours=8), conversation=conversation)
    _, state = app.on_login("e1", "pw-eng", {})
    return app, state, db


# ---------------- 가짜 응답 ----------------
def test_fake_answers_are_answer_responses():
    """가짜 응답은 진짜 응답 스키마(AnswerResponse)라서, RagPipeline 으로 바꿔도 화면 코드가 그대로 동작함"""
    assert fake_answer("벨트가 쏠려요").status == "answered"
    assert fake_answer("이상한 소리가 나요").status == "not_found"
    assert fake_photo_answer("a.png").status == "retake"


# ---------------- 말풍선·답변 카드 ----------------
def test_user_message_is_escaped():
    assert "<script>" not in render_user_message("<script>x</script>")
    assert "&lt;script&gt;" in render_user_message("<script>x</script>")


def test_answer_card_order_and_escape():
    html = render_answer_card(fake_answer("벨트"))
    positions = [html.index(w) for w in ("요약", "안전", "원인·조치", "그림", "출처·원문")]
    assert positions == sorted(positions)             # 요약 → 안전 문구 → 원인·조치 → 그림 → 출처·원문
    assert "①" in html and "원문 보기" in html
    evil = fake_answer("벨트").model_copy(update={"summary": "<img src=x onerror=alert(1)>"})
    assert "<img" not in render_answer_card(evil)


def test_answer_card_notice_for_not_found():
    html = render_answer_card(fake_answer("이상한 소리"))
    assert "문서에서 확인되지 않았습니다" in html and "다시 질문해주세요" in html


def test_render_answer_validates_dict_responses(env):
    """파이프라인이 돌려주는 응답 dict 는 ChatApp.render_answer 가 AnswerResponse 로 검사해서 그린다"""
    app, _, _ = env
    assert "wk-card" in app.render_answer(fake_answer("벨트").model_dump())
    bad = app.render_answer({"status": "answered"})           # 요약·출처가 없는 answered → 형식 오류
    assert "답변 형식이 올바르지 않아" in bad and "답변을 만들지 못했습니다" in bad
    assert "답변 형식이 올바르지 않아" in app.render_answer({})


def test_equipment_chip():
    assert "전체 매뉴얼" in render_equipment(None)
    assert "CV-1200" in render_equipment({"model_name": "CV-1200"}) and "wk-chip-on" in render_equipment({"model_name": "CV-1200"})
    assert "<b>" not in render_equipment({"model_name": "<b>x</b>"})
    assert "어떤 문제가 있나요?" in render_welcome()


# ---------------- ChatApp 동작 ----------------
def test_submit_appends_question_and_fake_answer(env):
    app, state, db = env
    history, state = app.on_submit("  3라인 벨트가 쏠려요  ", [], state)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert "3라인 벨트가 쏠려요" in history[0]["content"] and "wk-card" in history[1]["content"]
    assert db.history == []                           # 가짜 응답은 DB 대화 기록에 저장하지 않음


def test_submit_ignores_empty_question_and_logged_out(env):
    app, state, _ = env
    assert app.on_submit("   ", [], state)[0] == []
    assert app.on_submit(None, [], state)[0] == []
    assert app.on_submit("질문", [], {})[0] == []     # 로그인 전


def test_submit_keeps_given_history(env):
    app, state, _ = env
    first, state = app.on_submit("첫 질문", [], state)
    second, _ = app.on_submit("둘째 질문", first, state)
    assert len(second) == 4 and len(first) == 2       # 받은 history 는 고치지 않고 새 목록을 돌려줌


def test_reset_clears_equipment_but_keeps_user(env):
    app, state, _ = env
    state = app._conversation.set_equipment(state, Model("138-ACC"))
    state = app.reset_conversation(state)
    assert state["equipment"] is None and state["user"].employee_id == "e1"


def test_show_examples(env):
    app, _, _ = env
    templates, text = app.show_examples("모터가 뜨거워요")
    assert templates == list(EXAMPLE_QUESTIONS) and text == "모터가 뜨거워요"
    assert app.show_examples(None)[1] == ""


def test_screen_has_equipment_chip(env):
    app, state, _ = env
    state = app._conversation.set_equipment(state, Model("138-ACC"))
    assert "138-ACC" in app._screen(state)[-2]        # 끝에서 둘째: 현재 설비, 마지막: 로그인 안내


def test_chat_css_files_exist():
    assert all(Path(p).exists() for p in CHAT_CSS_PATHS)
    assert ChatApp.launch_options()["css_paths"][-len(CHAT_CSS_PATHS):] == list(CHAT_CSS_PATHS)


def test_theme_toggle_is_disabled_and_light_is_forced():
    """다크 모드는 화면별로 구현하는 동안 꺼 둠 (ui/login/theme.py 의 THEME_TOGGLE_ENABLED). 켜면 이 테스트도 같이 고친다."""
    from chatbot.ui.login.theme import THEME_INIT_JS, THEME_TOGGLE_ENABLED

    assert THEME_TOGGLE_ENABLED is False
    assert "classList.remove('dark')" in THEME_INIT_JS and "localStorage" not in THEME_INIT_JS
