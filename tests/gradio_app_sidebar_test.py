"""채팅 화면 설비 표시 · 대화 초기화 · 예시 질문 · 대화 기록 클릭 확인 (WBS 3.7.3). DB·화면 없이 검사함.

화면 틀·가짜 응답은 chat_ui_test.py(3.7.1), 대화 기록(show_history)은 login_ui_test.py 가 확인한다.

실행 (프로젝트 루트에서):
    pytest tests/gradio_app_sidebar_test.py -v
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # login_ui_test.env (가짜 DB로 만든 ChatApp)

import gradio as gr  # noqa: E402
import pytest  # noqa: E402
from login_ui_test import env  # noqa: E402,F401  (pytest fixture)

from chatbot.core.schemas import Model, User  # noqa: E402
from chatbot.ui.chat import (  # noqa: E402
    EXAMPLE_QUESTIONS,
    build_chat_area,
    build_sidebar,
    fake_answer,
    history_key,
    render_equipment,
    render_history,
)
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



# ---------------- 대화 기록 (C-03): 목록 · 항목 클릭 ----------------
NOW = datetime(2026, 10, 30, 9, 0)


def test_render_history_items_are_clickable_and_show_time():
    rows = [
        {"created_at": datetime(2026, 10, 30, 8, 47), "question": "<b>벨트</b>"},
        {"created_at": datetime(2026, 10, 29, 16, 20), "question": "모터"},
        {"created_at": "2026-10-27 09:13:00", "question": "체인"},
    ]
    html = render_history(rows, now=NOW)
    assert html.count("class='history-item'") == 3 and "data-index='2'" in html
    assert "data-key='2026-10-30 08:47:00|&lt;b&gt;벨트&lt;/b&gt;'" in html   # 저장 시각 + 질문 (escape)
    assert "오늘 08:47" in html and "어제 16:20" in html and "10/27 09:13" in html   # Figma 시각 표시
    assert "<b>벨트</b>" not in html and "&lt;b&gt;" in html
    assert render_history([]) == "<p class='sb-empty'>아직 대화 기록이 없습니다.</p>"


def test_history_list_sends_click_with_index_and_time():
    with gr.Blocks():
        sidebar = build_sidebar()
    js = sidebar.history.js_on_load   # 목록 전체에 걸어 둔 클릭 → history.click 이벤트
    assert "trigger('click'" in js and "dataset.index" in js and "dataset.key" in js


def save(conversation, state, question, answer=None):
    return conversation.save_history(
        state, {"question": question, "answer": answer or fake_answer(question).model_dump(mode="json")}
    )


def test_open_history_shows_question_and_answer(env):  # noqa: F811
    app, _, conversation = env
    _, state = app.on_login("e1", "pw-eng", {})
    for question in ("벨트가 미끄러져요", "모터가 너무 뜨거워요"):
        state = save(conversation, state, question)
    rows = app.show_history(state)                      # 최신순: 모터 → 벨트
    messages = app.open_history(state, 1, history_key(rows[1]))
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert "벨트가 미끄러져요" in messages[0]["content"] and "wk-card" in messages[1]["content"]


def test_open_history_finds_moved_item_by_time(env):  # noqa: F811
    app, _, conversation = env
    _, state = app.on_login("e1", "pw-eng", {})
    state = save(conversation, state, "벨트가 미끄러져요")
    key = history_key(app.show_history(state)[0])     # 목록을 그릴 때는 0번 (같은 초에 저장돼도 질문으로 구분)
    state = save(conversation, state, "모터가 너무 뜨거워요")   # 그사이 새 대화가 맨 위로 들어옴
    assert "벨트가 미끄러져요" in app.open_history(state, 0, key)[0]["content"]


def test_open_history_not_found_or_logged_out(env):  # noqa: F811
    app, _, conversation = env
    _, state = app.on_login("e1", "pw-eng", {})
    state = save(conversation, state, "벨트가 미끄러져요")
    assert app.open_history(state, 3, "없는 기록") is None
    assert app.open_history(state, "x", None) is None
    assert app.open_history({}, 0, None) is None     # 로그인 전


def test_open_history_broken_answer_shows_notice(env):  # noqa: F811
    app, db, _ = env
    _, state = app.on_login("e1", "pw-eng", {})
    db.history.append({"id": 1, "employee_id": "e1", "created_at": NOW, "question": "깨진 답변", "answer": "{"})
    messages = app.open_history(state, 0, history_key({"created_at": NOW, "question": "깨진 답변"}))
    assert "깨진 답변" in messages[0]["content"] and "답변 형식이 올바르지 않아" in messages[1]["content"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
