"""AdminDashboard.load_logs 실제 MySQL(Aiven) 확인 (WBS 3.8.1)

- .env 의 DB(DB_NAME)에 테스트 로그를 넣고 load_logs 로 읽어 본 뒤, 넣은 행은 끝에 지운다.
- 테스트 로그는 질문이 "[TEST load_logs]" 로 시작하고 employee_id 는 비워 둔다 (사원 계정과 무관).
- 날짜는 2000-01-01 ~ 03 으로 넣어서 실제 운영 로그와 섞이지 않게 한다.

실행 (환경변수 LIVE_DB=1 일 때만 돈다. 평소 pytest 에서는 건너뜀):
    Windows cmd : set "LIVE_DB=1" && python -m pytest tests/load_logs_live_test.py -v -s
    Git Bash    : LIVE_DB=1 python -m pytest tests/load_logs_live_test.py -v -s
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chatbot.ui.dashboard import LOG_TABLE_COLUMNS, AdminDashboard

pytestmark = pytest.mark.skipif(os.environ.get("LIVE_DB", "").strip() != "1",
                                reason="실제 DB 확인은 LIVE_DB=1 일 때만")

TAG = "[TEST load_logs]"
INSERT_SQL = (
    "INSERT INTO chat_logs (source, employee_id, created_at, question, input_type, question_type, "
    "equipment_model, kb_ids, top_score, not_found, safety_included, latency_ms) "
    "VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


@pytest.fixture(scope="module")
def db():
    from chatbot.core.config import Settings
    from chatbot.db.db import Database

    db = Database().connect(Settings.load())
    print(f"\n[DB] {Settings.load().db_name} 에 연결")
    _cleanup(db)                     # 지난번에 실패해서 남은 테스트 행이 있으면 먼저 지움
    yield db
    _cleanup(db)
    db.close()


@pytest.fixture(scope="module")
def ids(db):
    """테스트 로그 4줄 + kb_gaps 1줄을 넣고 log_id 를 돌려준다"""
    rows = {
        "start": ("user", "2000-01-01 00:00:00", f"{TAG} 시작일 0시", "text", "troubleshooting", "138-ACC",
                  ["TS-HYTROL138ACC-T01-04"], 0.82, False, True, {"total": 3200}),
        "end": ("user", "2000-01-02 23:59:59", f"{TAG} 종료일 밤", "nameplate", None, None, None, None, True, False, None),
        "eval": ("eval", "2000-01-02 12:00:00", f"{TAG} 평가 실행", "text", None, None, None, None, False, False, None),
        "after": ("user", "2000-01-03 00:00:00", f"{TAG} 다음 날", "text", None, None, None, None, False, False, None),
    }
    ids = {key: db.execute(INSERT_SQL, values) for key, values in rows.items()}
    db.execute("INSERT INTO kb_gaps (log_id, reviewed, kb_update_needed) VALUES (%s, TRUE, TRUE)", (ids["end"],))
    return ids


@pytest.fixture(scope="module")
def logs(db, ids):
    logs = AdminDashboard().load_logs(date(2000, 1, 1), date(2000, 1, 2), db)
    logs = logs[logs["question"].str.startswith(TAG)]
    print(logs[["log_id", "created_at", "question", "not_found", "reviewed", "kb_update_needed"]].to_string())
    return logs


def test_period_and_eval_excluded(logs):
    """시작일 0시 · 종료일 밤은 들어오고, 평가 실행 · 다음 날은 빠진다 (최신순)"""
    assert list(logs["question"]) == [f"{TAG} 종료일 밤", f"{TAG} 시작일 0시"]


def test_columns_and_types_from_mysql(logs):
    assert list(logs.columns) == LOG_TABLE_COLUMNS
    first = logs.set_index("question").loc[f"{TAG} 시작일 0시"]
    assert isinstance(first["created_at"], datetime)
    assert first["kb_ids"] == ["TS-HYTROL138ACC-T01-04"] and first["latency_ms"] == {"total": 3200}
    assert first["equipment_model"] == "138-ACC" and abs(first["top_score"] - 0.82) < 1e-6
    for column in ("not_found", "safety_included", "reviewed", "kb_update_needed"):
        assert logs[column].dtype == bool


def test_kb_gaps_joined(logs):
    by_question = logs.set_index("question")
    assert by_question.loc[f"{TAG} 종료일 밤", "reviewed"] and by_question.loc[f"{TAG} 종료일 밤", "kb_update_needed"]
    assert not by_question.loc[f"{TAG} 시작일 0시", "reviewed"]          # kb_gaps 행 없음 → false


def _cleanup(db):
    """테스트 행만 지운다 (kb_gaps 는 chat_logs 를 지우면 같이 지워짐: ON DELETE CASCADE)"""
    db.execute("DELETE FROM chat_logs WHERE question LIKE %s", (f"{TAG}%",))
