"""AdminDashboard.load_logs 동작 확인 (WBS 3.8.1). MySQL 없이 sqlite 메모리 DB(tests/sqlite_db.py)로 검사함.

실행 (프로젝트 루트에서):
    python -m pytest tests/load_logs_test.py -v
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # sqlite_db

from sqlite_db import SqliteDb

from chatbot.ui.dashboard import LOAD_LOGS_SQL, LOG_TABLE_COLUMNS, AdminDashboard


def add_log(db, created_at, source="user", question="모터가 뜨거워요", **values):
    """chat_logs 한 줄 넣기 (값은 schema.sql 열 이름)"""
    row = {"source": source, "employee_id": None if source == "eval" else "e1", "created_at": created_at,
           "question": question, "input_type": "text", "not_found": 0, "safety_included": 1, **values}
    for key in ("kb_ids", "latency_ms"):
        if key in row and not isinstance(row[key], str):
            row[key] = json.dumps(row[key])
    columns = ", ".join(row)
    return db.execute(f"INSERT INTO chat_logs ({columns}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))


@pytest.fixture
def db():
    db = SqliteDb()
    db.execute("INSERT INTO chat_logs (employee_id, question, input_type) VALUES (NULL, 'seed', 'text')")  # log_id 1 (기간 밖)
    db.execute("UPDATE chat_logs SET created_at = '2026-10-01 09:00:00' WHERE log_id = 1")
    return db


@pytest.fixture
def board():
    return AdminDashboard()


def test_period_includes_start_and_end_day(board, db):
    add_log(db, "2026-10-27 00:00:00", question="시작일 0시")
    add_log(db, "2026-10-28 23:59:59", question="종료일 밤")
    add_log(db, "2026-10-26 23:59:59", question="하루 전")
    add_log(db, "2026-10-29 00:00:00", question="다음 날")
    logs = board.load_logs(date(2026, 10, 27), date(2026, 10, 28), db)
    assert list(logs["question"]) == ["종료일 밤", "시작일 0시"]           # 최신순


def test_eval_logs_are_excluded(board, db):
    add_log(db, "2026-10-28 10:00:00", question="실사용")
    add_log(db, "2026-10-28 11:00:00", source="eval", question="평가 실행")
    logs = board.load_logs(date(2026, 10, 28), date(2026, 10, 28), db)
    assert list(logs["question"]) == ["실사용"]


def test_kb_gaps_review_state_joined(board, db):
    reviewed = add_log(db, "2026-10-28 10:00:00", question="검토함", not_found=1)
    add_log(db, "2026-10-28 11:00:00", question="검토 안 함", not_found=1)
    db.execute("INSERT INTO kb_gaps (log_id, reviewed, kb_update_needed, reviewed_by) VALUES (%s, 1, 1, 'a1')", (reviewed,))
    logs = board.load_logs(date(2026, 10, 28), date(2026, 10, 28), db).set_index("question")
    assert bool(logs.loc["검토함", "reviewed"]) and bool(logs.loc["검토함", "kb_update_needed"])
    assert not logs.loc["검토 안 함", "reviewed"] and not logs.loc["검토 안 함", "kb_update_needed"]   # kb_gaps 없으면 false


def test_columns_and_types(board, db):
    add_log(db, "2026-10-28 10:00:00", input_type="nameplate", question_type="troubleshooting",
            equipment_model="138-ACC", kb_ids=["TS-HYTROL138ACC-T01-04"], top_score=0.82,
            latency_ms={"total": 3200})
    logs = board.load_logs(date(2026, 10, 28), date(2026, 10, 28), db)
    assert list(logs.columns) == LOG_TABLE_COLUMNS
    row = logs.iloc[0]
    assert isinstance(row["created_at"], datetime)
    assert row["kb_ids"] == ["TS-HYTROL138ACC-T01-04"] and row["latency_ms"] == {"total": 3200}
    assert not row["not_found"]
    assert logs["safety_included"].dtype == bool and logs["reviewed"].dtype == bool
    assert row["equipment_model"] == "138-ACC" and row["input_type"] == "nameplate"


def test_empty_json_becomes_empty_list_and_dict(board, db):
    add_log(db, "2026-10-28 10:00:00")          # kb_ids · latency_ms 없음 (NULL)
    row = board.load_logs(date(2026, 10, 28), date(2026, 10, 28), db).iloc[0]
    assert row["kb_ids"] == [] and row["latency_ms"] == {}


def test_no_logs_returns_empty_table_with_columns(board, db):
    logs = board.load_logs(date(2026, 1, 1), date(2026, 1, 7), db)
    assert logs.empty and list(logs.columns) == LOG_TABLE_COLUMNS


def test_reversed_period_and_string_dates(board, db):
    add_log(db, "2026-10-28 10:00:00")
    assert len(board.load_logs("2026-10-28", "2026-10-27", db)) == 1         # 거꾸로 넣어도 같은 기간
    afternoon = datetime(2026, 10, 28, 15)  # noqa: DTZ001 (chat_logs.created_at 처럼 시간대 없음)
    assert len(board.load_logs(afternoon, date(2026, 10, 28), db)) == 1


def test_values_go_as_parameters(board, db):
    """기간 값은 SQL 에 직접 넣지 않고 매개변수로 넘김 (SQL 인젝션 방지)"""
    board.load_logs(date(2026, 10, 28), date(2026, 10, 28), db)
    assert db.statements[-1] == LOAD_LOGS_SQL and "2026" not in LOAD_LOGS_SQL
