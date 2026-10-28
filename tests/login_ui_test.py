"""로그인 화면 로직·대화 상태 확인 (로컬 전용). DB·화면 없이 메모리 가짜 DB로 검사함.

실행 (프로젝트 루트에서):
    pytest tests/login_ui_test.py -v
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # auth_test.FakeDb (users·sessions 가짜 DB)

import pytest

from auth_test import FakeDb as AuthFakeDb
from chatbot.core.auth import AuthManager
from chatbot.core.schemas import Model, User
from chatbot.core.state import ConversationState
from chatbot.ui.gradio_app import ChatApp
from chatbot.ui.login import MSG_EMPTY, MSG_INVALID, render_history, render_user_info


class FakeDb(AuthFakeDb):
    """auth_test.FakeDb 에 chat_history 를 더한 가짜 Database"""

    def __init__(self):
        super().__init__()
        self.history = []   # {"id", "employee_id", "created_at", "question", "answer"}

    def fetch(self, sql, params=()):
        if "FROM chat_history" in sql:
            self._check()
            rows = [r for r in self.history if r["employee_id"] == params[0]]
            rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
            return [{k: r[k] for k in ("created_at", "question", "answer")} for r in rows[: params[1]]]
        return super().fetch(sql, params)

    def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO chat_history"):
            self._check()
            self.history.append(
                {"id": len(self.history) + 1, "employee_id": params[0], "created_at": datetime.now(),
                 "question": params[1], "answer": params[2]}
            )
            return len(self.history)
        return super().execute(sql, params)


@pytest.fixture
def env():
    db = FakeDb()
    auth = AuthManager(db, session_hours=8)
    conversation = ConversationState(db)
    return ChatApp(auth=auth, conversation=conversation), db, conversation


# ---------------- on_login ----------------
def test_login_empty_input(env):
    app, db, _ = env
    for emp, pw in [("", "pw-eng"), ("e1", ""), ("  ", "  "), (None, None)]:
        token, state = app.on_login(emp, pw, {})
        assert token is None and state["login_error"] == MSG_EMPTY and "user" not in state
    assert db.sessions == {}


def test_login_wrong_password_or_unknown_id(env):
    app, db, _ = env
    for emp, pw in [("e1", "wrong"), ("nobody", "pw-eng")]:
        token, state = app.on_login(emp, pw, {})
        assert token is None and state["login_error"] == MSG_INVALID and "user" not in state
    assert db.sessions == {}   # 실패하면 세션을 만들지 않음


def test_login_success_creates_session_and_state(env):
    app, db, _ = env
    token, state = app.on_login(" e1 ", "pw-eng", {"login_error": MSG_INVALID})
    assert len(token) == 64 and token in db.sessions and db.sessions[token][0] == "e1"
    assert state["user"] == User("e1", "엔지니어", "user")
    assert "login_error" not in state and state["admin_mode"] is False


def test_login_does_not_mutate_given_state(env):
    app, _, _ = env
    given = {"equipment": "x"}
    app.on_login("e1", "wrong", given)
    assert given == {"equipment": "x"}


# ---------------- on_load (세션 유지) ----------------
def test_load_restores_valid_session(env):
    app, db, _ = env
    token, _ = app.on_login("a1", "pw-admin", {})
    user, state = app.on_load(token)
    assert user.is_admin and state["user"] == user


def test_load_without_or_with_bad_token(env):
    app, db, _ = env
    for token in (None, "", "f" * 64):
        user, state = app.on_load(token)
        assert user is None and state == {}


def test_load_expired_session(env):
    app, db, _ = env
    token, _ = app.on_login("e1", "pw-eng", {})
    emp, _exp = db.sessions[token]
    db.sessions[token] = (emp, datetime(2000, 1, 1))
    assert app.on_load(token)[0] is None


# ---------------- on_logout ----------------
def test_logout_clears_session_state_and_keeps_history(env):
    app, db, conv = env
    token, state = app.on_login("e1", "pw-eng", {})
    state = conv.set_equipment(state, Model("138-ACC"))
    conv.save_history(state, {"question": "모터가 뜨거워요", "answer": {"summary": "x"}})
    ok, state = app.on_logout(token, state)
    assert ok and token not in db.sessions
    assert state["user"] is None and state["equipment"] is None and state["admin_mode"] is False   # ConversationState.clear_user 결과
    assert len(db.history) == 1                      # DB 의 대화 기록은 그대로
    assert app.on_load(token)[0] is None             # 같은 토큰으로 다시 들어올 수 없음


def test_logout_survives_db_error(env):
    app, db, _ = env
    token, state = app.on_login("e1", "pw-eng", {})
    db.broken = True
    ok, state = app.on_logout(token, state)
    assert ok and state["user"] is None


# ---------------- 관리자 전환 ----------------
def test_admin_mode(env):
    app, _, _ = env
    _, user_state = app.on_login("e1", "pw-eng", {})
    assert app.change_admin_mode(user_state)[0] is False and not user_state.get("admin_mode")
    _, admin_state = app.on_login("a1", "pw-admin", {})
    ok, admin_state = app.change_admin_mode(admin_state)
    assert ok and admin_state["admin_mode"] is True
    assert app.exit_admin_mode(admin_state)["admin_mode"] is False
    assert app.change_admin_mode({})[0] is False     # 로그인 전에는 전환 불가


# ---------------- 대화 기록 (사원번호별) ----------------
def test_history_is_per_employee_and_newest_first(env):
    app, db, conv = env
    _, s1 = app.on_login("e1", "pw-eng", {})
    _, s2 = app.on_login("a1", "pw-admin", {})
    conv.save_history(s1, {"question": "첫 질문", "answer": {"n": 1}})
    conv.save_history(s1, {"question": "둘째 질문", "answer": {"n": 2}})
    conv.save_history(s2, {"question": "관리자 질문", "answer": {"n": 3}})
    assert [r["question"] for r in app.show_history(s1)] == ["둘째 질문", "첫 질문"]
    assert [r["question"] for r in app.show_history(s2)] == ["관리자 질문"]
    assert app.show_history({}) == []                # 로그인 전에는 기록이 없음


def test_history_visible_after_relogin(env):
    app, db, conv = env
    token, state = app.on_login("e1", "pw-eng", {})
    conv.save_history(state, {"question": "벨트가 쏠려요", "answer": {}})
    app.on_logout(token, state)
    _, state = app.on_login("e1", "pw-eng", {})
    assert [r["question"] for r in app.show_history(state)] == ["벨트가 쏠려요"]


def test_save_history_survives_db_error(env):
    app, db, conv = env
    _, state = app.on_login("e1", "pw-eng", {})
    db.broken = True
    state = conv.save_history(state, {"question": "q", "answer": {}})   # 예외 없이 지나가고, 상태(로그인 사원)는 그대로
    assert state["user"].employee_id == "e1"


# ---------------- 화면 ----------------
def test_render_history_escapes_html():
    html = render_history([{"created_at": datetime(2026, 1, 2, 3, 4), "question": "<script>x</script>"}])
    assert "<script>" not in html and "&lt;script&gt;" in html and "2026-01-02 03:04" in html
    assert "대화 기록이 없습니다" in render_history([])


def test_render_user_info_has_name_and_id_only():
    html = render_user_info(User("e1", "<b>이름</b>", "user"))
    assert "e1" in html and "&lt;b&gt;" in html and "<b>" not in html
    assert render_user_info(None) == ""


def test_screen_shows_exactly_one_view(env):
    app, _, _ = env
    login, chat, admin, admin_btn, *_rest, error = app._screen({})
    assert (login["visible"], chat["visible"], admin["visible"], admin_btn["visible"]) == (True, False, False, False)
    _, state = app.on_login("e1", "wrong", {})
    assert MSG_INVALID in app._screen(state)[-1]["value"] and app._screen(state)[-1]["visible"]
    _, state = app.on_login("a1", "pw-admin", {})
    login, chat, admin, admin_btn, *_ = app._screen(state)
    assert (login["visible"], chat["visible"], admin["visible"], admin_btn["visible"]) == (False, True, False, True)
    app.change_admin_mode(state)
    login, chat, admin, *_ = app._screen(state)
    assert (login["visible"], chat["visible"], admin["visible"]) == (False, False, True)


def test_build_ui_and_css_files_exist(env):
    app, _, _ = env
    demo = app.build_ui()
    assert type(demo).__name__ == "Blocks"
    options = ChatApp.launch_options()
    assert all(Path(p).exists() for p in options["css_paths"])
