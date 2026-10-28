"""state.py 동작 확인. 실제 MySQL 대신 메모리 가짜 DB로 검사함 (Aiven 접속 없음).

실행 (프로젝트 루트에서):
    python -m pytest tests/state_test.py -v
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 프로젝트 루트에서 chatbot 패키지 찾기

from chatbot.core.schemas import Model, User
from chatbot.core.state import ConversationState, new_state

ENGINEER = User(employee_id="e1", name="엔지니어", role="user")
ADMIN = User(employee_id="a1", name="관리자", role="admin")
ACC = Model(model_name="138-ACC", aliases=["ACC"], doc_ids=["hytrol_138acc"])
ANSWER = {"not_found": False, "summary": "과부하·저전압·감속기 윤활유 부족을 확인하세요."}


class FakeDb:
    """ConversationState 가 보내는 chat_history SQL 만 알아듣는 가짜 Database"""

    def __init__(self):
        self.rows = []          # chat_history 테이블 대신
        self.broken = False
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if self.broken:
            raise ConnectionError("db down")
        assert sql.startswith("INSERT INTO chat_history")
        employee_id, question, answer = params
        created = datetime(2026, 10, 28, 9, 0) + timedelta(minutes=len(self.rows))  # noqa: DTZ001 (MySQL DATETIME 처럼 시간대 없음)
        self.rows.append({"id": len(self.rows) + 1, "employee_id": employee_id, "created_at": created,
                          "question": question, "answer": answer})
        return len(self.rows)

    def fetch(self, sql, params=()):
        self.sql.append(sql)
        if self.broken:
            raise ConnectionError("db down")
        employee_id, limit = params
        rows = [r for r in self.rows if r["employee_id"] == employee_id]
        rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return rows[:limit]


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def cs(db):
    return ConversationState(db)


def logged_in(cs, user=ENGINEER):
    return cs.set_user(new_state(), user)


# ---------------------------------------------------------------- set_user · set_equipment
def test_set_user_keeps_user_and_starts_in_chat(cs):
    state = cs.set_user({"admin_mode": True}, ADMIN)
    assert state["user"] == ADMIN and state["admin_mode"] is False


def test_set_equipment_saves_model_for_pipeline_and_retriever(cs):
    state = cs.set_equipment(logged_in(cs), ACC)
    assert state["equipment"] == {"model_name": "138-ACC", "aliases": ["ACC"], "doc_ids": ["hytrol_138acc"]}
    assert state["user"] == ENGINEER                          # 다른 칸은 그대로


def test_set_equipment_replaces_previous(cs):
    state = cs.set_equipment(logged_in(cs), ACC)
    state = cs.set_equipment(state, Model(model_name="L500", doc_ids=["preco_l_series"]))
    assert state["equipment"]["model_name"] == "L500"


def test_works_with_empty_state(cs):
    """Gradio 가 처음에 None 이나 빈 dict 를 넘겨도 오류 없이 기본 칸을 채움"""
    for empty in (None, {}):
        state = cs.set_equipment(empty, ACC)
        assert set(state) == {"user", "admin_mode", "equipment"}


def test_input_state_not_changed(cs):
    """받은 state 는 그대로 두고 새 dict 를 돌려줌"""
    before = logged_in(cs)
    copy = dict(before)
    cs.set_equipment(before, ACC)
    assert before == copy


# ---------------------------------------------------------------- clear · clear_user
def test_clear_resets_equipment_but_keeps_user(cs, db):
    state = cs.set_equipment(logged_in(cs, ADMIN), ACC)
    state["admin_mode"] = True
    state = cs.save_history(state, {"question": "모터가 뜨거워요", "answer": ANSWER})
    state = cs.clear(state)
    assert state["equipment"] is None
    assert state["user"] == ADMIN and state["admin_mode"] is True
    assert len(db.rows) == 1                                   # DB 기록은 유지


def test_clear_user_resets_everything_but_keeps_db(cs, db):
    state = cs.set_equipment(logged_in(cs, ADMIN), ACC)
    state["admin_mode"] = True
    state = cs.save_history(state, {"question": "모터가 뜨거워요", "answer": ANSWER})
    state = cs.clear_user(state)
    assert state == new_state()
    assert len(db.rows) == 1
    assert cs.load_history("a1", 10)[0]["question"] == "모터가 뜨거워요"   # 다시 로그인하면 보임


# ---------------------------------------------------------------- save_history
def test_save_history_saves_to_db(cs, db):
    before = logged_in(cs)
    state = cs.save_history(before, {"question": " 모터가 뜨거워요 ", "answer": ANSWER})
    assert state == before                                     # state 는 바뀌지 않음 (화면 채팅은 Gradio 가 관리)
    row = db.rows[0]
    assert row["employee_id"] == "e1" and row["question"] == "모터가 뜨거워요" and row["answer"] == ANSWER


def test_save_history_keeps_order(cs, db):
    state = logged_in(cs)
    for q in ("첫 질문", "두 번째 질문"):
        state = cs.save_history(state, {"question": q, "answer": ANSWER})
    assert [r["question"] for r in db.rows] == ["첫 질문", "두 번째 질문"]


def test_save_history_skips_empty_question(cs, db):
    cs.save_history(logged_in(cs), {"question": "   ", "answer": ANSWER})
    assert db.rows == []


def test_save_history_without_login_not_saved(cs, db):
    state = cs.save_history(new_state(), {"question": "모터가 뜨거워요", "answer": ANSWER})
    assert state == new_state() and db.rows == []


def test_save_history_db_failure_keeps_chat(cs, db, capsys):
    db.broken = True
    state = cs.save_history(logged_in(cs), {"question": "모터가 뜨거워요", "answer": ANSWER})
    assert state["user"] == ENGINEER                           # 오류 없이 state 를 돌려줌
    out = capsys.readouterr().out
    assert "대화 기록 저장 실패" in out and "e1" not in out     # 사원번호는 출력 안 함


def test_without_db_does_nothing():
    cs = ConversationState()
    state = cs.save_history(cs.set_user(None, ENGINEER), {"question": "모터가 뜨거워요", "answer": ANSWER})
    assert state["user"] == ENGINEER
    assert cs.load_history("e1", 10) == []


# ---------------------------------------------------------------- load_history
def test_load_history_newest_first_and_only_mine(cs):
    mine, other = logged_in(cs, ENGINEER), logged_in(cs, ADMIN)
    for q in ("1번", "2번", "3번"):
        mine = cs.save_history(mine, {"question": q, "answer": ANSWER})
    cs.save_history(other, {"question": "관리자 질문", "answer": ANSWER})
    rows = cs.load_history("e1", 10)
    assert [r["question"] for r in rows] == ["3번", "2번", "1번"]
    assert set(rows[0]) == {"created_at", "question", "answer"}


def test_load_history_limit(cs, db):
    state = logged_in(cs)
    for i in range(5):
        state = cs.save_history(state, {"question": f"{i}번", "answer": ANSWER})
    assert len(cs.load_history("e1", 2)) == 2
    assert len(cs.load_history("e1", 0)) == 1                 # 0 이하는 1개
    cs.load_history("e1", 10_000)
    assert "LIMIT %s" in db.sql[-1]                            # 값은 매개변수로 (SQL 에 직접 넣지 않음)


def test_load_history_empty_or_failure(cs, db):
    assert cs.load_history("", 10) == []
    assert cs.load_history("nobody", 10) == []
    db.broken = True
    assert cs.load_history("e1", 10) == []