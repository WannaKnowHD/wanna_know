"""채팅 화면 설비 표시 · 대화 초기화 · 예시 질문 확인 (WBS 3.7.3). DB·화면 없이 검사함.

화면 틀·가짜 응답은 chat_ui_test.py(3.7.1), 대화 기록(show_history)은 login_ui_test.py 가 확인한다.

실행 (프로젝트 루트에서):
    pytest tests/gradio_app_sidebar_test.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # login_ui_test.env (가짜 DB로 만든 ChatApp)

import gradio as gr  # noqa: E402
import pytest  # noqa: E402
from login_ui_test import env  # noqa: E402,F401  (pytest fixture)

from chatbot.core.schemas import Model, User  # noqa: E402
from chatbot.ui.chat import EXAMPLE_QUESTIONS, build_chat_area, render_equipment  # noqa: E402
from chatbot.ui.gradio_app import ChatApp  # noqa: E402

USER = User("e1", "사원", "user")
ACC = Model("138-ACC", ["ACC"], ["hytrol_138acc"])


# ---------------- 예시 질문 (Q-06) ----------------
def test_show_examples_lists_templates_and_fills_input():
    app = ChatApp()
    templates, text = app.show_examples(None)
    assert templates == list(EXAMPLE_QUESTIONS) and text == ""
    assert app.show_examples(" 벨트가 미끄러져요 ")[1] == "벨트가 미끄러져요"


def test_example_chips_match_show_examples():
    with gr.Blocks():
        area = build_chat_area()
    assert [button.value for button in area.example_buttons] == ChatApp().show_examples(None)[0]


@pytest.mark.parametrize("text", EXAMPLE_QUESTIONS)
def test_example_questions_are_short(text):
    assert 0 < len(text) <= 20  # 칩 한 줄에 들어가는 길이


# ---------------- 설비 표시 (C-01) ----------------
def test_show_equipment_with_model():
    app = ChatApp()
    html = app.show_equipment(app._conversation.set_equipment({"user": USER}, ACC))  # {"model_name", "aliases", "doc_ids"}
    assert "138-ACC" in html and "wk-chip-on" in html and "전체 매뉴얼" not in html


def test_show_equipment_without_model():
    for state in ({}, {"equipment": None}, None):
        html = ChatApp().show_equipment(state)
        assert "전체 매뉴얼" in html and "wk-chip-on" not in html


def test_show_equipment_accepts_model_object():
    app = ChatApp()
    assert app.show_equipment({"equipment": ACC}) == app.show_equipment(app._conversation.set_equipment({}, ACC))


def test_show_equipment_escapes_model_name():
    html = ChatApp().show_equipment({"equipment": {"model_name": "<b>x</b>"}})
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html


# ---------------- 대화 초기화 (C-02) ----------------
def test_reset_conversation_keeps_user_and_clears_equipment():
    app = ChatApp()
    state = app._conversation.set_equipment({"user": USER, "admin_mode": True}, ACC)
    new_state = app.reset_conversation(state)
    assert new_state["equipment"] is None
    assert new_state["user"] == USER and new_state["admin_mode"] is True
    assert state["equipment"]["model_name"] == "138-ACC"  # 받은 dict 는 고치지 않음
    assert app.show_equipment(new_state) == render_equipment(None)


# ---------------- 로그인 · 로그아웃 때 다시 그리기 ----------------
def test_screen_redraws_equipment_on_login_and_logout(env):  # noqa: F811
    app, _, conversation = env
    token, state = app.on_login("e1", "pw-eng", {})
    *_, equipment, _error = app._screen(state)   # 끝에서 둘째: 현재 설비, 마지막: 로그인 안내
    assert equipment == render_equipment(None)
    state = conversation.set_equipment(state, ACC)
    assert "138-ACC" in app._screen(state)[-2]
    _, state = app.on_logout(token, state)
    assert app._screen(state)[-2] == render_equipment(None)


def test_build_ui(env):  # noqa: F811
    app, _, _ = env
    assert type(app.build_ui()).__name__ == "Blocks"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
