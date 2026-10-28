"""테스트·화면 데모용 sqlite 메모리 DB. db.py의 Database처럼 execute · fetch만 있다.

chat_logs · kb_gaps는 chatbot/db/schema.sql과 같은 열로 만든다 (MySQL ENUM은 CHECK로 흉내).
schema.sql 열이 바뀌면 logger_test의 test_sqlite_tables_match_schema_sql이 알려 준다.
"""

import re
import sqlite3
from pathlib import Path

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "chatbot" / "db" / "schema.sql"

SQLITE_SCHEMA = """
CREATE TABLE chat_logs (
  log_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source           TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'eval')),
  employee_id      TEXT,
  created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  question         TEXT NOT NULL,
  input_type       TEXT NOT NULL CHECK (input_type IN ('text', 'nameplate', 'damage', 'other')),
  question_type    TEXT,
  equipment_model  TEXT CHECK (length(equipment_model) <= 50),
  kb_ids           TEXT,
  top_score        REAL,
  not_found        INTEGER NOT NULL DEFAULT 0,
  safety_included  INTEGER NOT NULL DEFAULT 0,
  confidence       REAL,
  latency_ms       TEXT,
  tokens_in        INTEGER,
  tokens_out       INTEGER,
  error            TEXT CHECK (length(error) <= 255)
);
CREATE TABLE kb_gaps (
  gap_id            INTEGER PRIMARY KEY AUTOINCREMENT,
  log_id            INTEGER NOT NULL UNIQUE REFERENCES chat_logs(log_id),
  reviewed          INTEGER NOT NULL DEFAULT 0,
  kb_update_needed  INTEGER NOT NULL DEFAULT 0,
  reviewed_by       TEXT,
  reviewed_at       TEXT
);
"""


class SqliteDb:
    """MySQL 대신 쓰는 메모리 DB. MySQL 전용 구문 몇 개만 sqlite 문법으로 바꿔 실행한다."""

    def __init__(self):
        # Gradio 데모는 요청마다 다른 스레드에서 부르므로 같은 연결을 스레드끼리 나눠 쓴다
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SQLITE_SCHEMA)
        self.broken = False
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple = ()) -> int:
        self._check()
        self.statements.append(sql)
        cursor = self.conn.execute(to_sqlite(sql), params)
        self.conn.commit()
        if sql.lstrip().upper().startswith("INSERT"):
            return cursor.lastrowid
        return cursor.rowcount

    def fetch(self, sql: str, params: tuple = ()) -> list[dict]:
        self._check()
        self.statements.append(sql)
        return [dict(row) for row in self.conn.execute(to_sqlite(sql), params)]

    def _check(self) -> None:
        if self.broken:
            raise ConnectionError("db down")


def to_sqlite(sql: str) -> str:
    """%s → ?, NOW() → CURRENT_TIMESTAMP, MySQL upsert → sqlite upsert."""
    sql = sql.replace("%s", "?").replace("NOW()", "CURRENT_TIMESTAMP")
    return sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT(log_id) DO UPDATE SET")


def schema_sql_columns(table: str) -> list[str]:
    """chatbot/db/schema.sql의 CREATE TABLE에서 열 이름만 뽑는다 (INDEX · 키 · 제약 줄 제외).

    ERD v6처럼 FK를 CONSTRAINT ... / REFERENCES ... 두 줄로 쓴 형식도 건너뛴다.
    닫는 괄호 뒤의 테이블 옵션(DEFAULT CHARSET=utf8mb4 등)은 건너뛴다.
    """
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\)[^;]*;", text, re.S
    )
    columns = []
    for line in match.group(1).splitlines():
        line = line.split("--")[0].strip()
        if not line or line.split()[0].upper() in (
            "INDEX",
            "FOREIGN",
            "PRIMARY",
            "UNIQUE",
            "CONSTRAINT",
            "REFERENCES",
        ):
            continue
        columns.append(line.split()[0])
    return columns


def sqlite_columns(db: SqliteDb, table: str) -> list[str]:
    return [row["name"] for row in db.conn.execute(f"PRAGMA table_info({table})")]
