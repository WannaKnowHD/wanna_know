"""
db.py 테스트 (Database.connect · execute · fetch · 연결 풀)

- 실제 DB 에 접속하지 않는다. pymysql.connect 를 가짜 연결로 바꿔서 검사한다
- .env 를 읽지 않도록 Settings 대신 접속 정보만 담은 가짜 설정을 쓴다
실행: python -m pytest tests/db_test.py -v
"""
import json
import threading
from types import SimpleNamespace

import pymysql
import pytest
from pymysql.constants import FIELD_TYPE

import chatbot.db.db as db_module
from chatbot.db.db import Database, DatabaseError

FAKE_SETTINGS = SimpleNamespace(
    db_host="db.example.test", db_port=3306, db_user="tester", db_password="test-only",
    db_name="testdb", db_ssl_ca=None,
)


class FakeCursor:
    """SQL 과 값을 기록하고, 정해 둔 결과를 돌려주는 가짜 커서."""

    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self.lastrowid = 0
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        if self.conn.fail_with is not None:
            raise self.conn.fail_with
        self.conn.executed.append((sql, params))
        if sql.startswith("SELECT"):
            self._rows = [dict(r) for r in self.conn.rows]
            self.description = self.conn.description
            return len(self._rows)
        self.lastrowid = self.conn.lastrowid
        return self.conn.affected

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.executed = []
        self.rows = []
        self.description = None
        self.lastrowid = 0
        self.affected = 1
        self.fail_with = None
        self.ping_error = None
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def ping(self, reconnect=True):
        if self.ping_error is not None:
            raise self.ping_error

    def close(self):
        self.closed = True


@pytest.fixture
def opened(monkeypatch):
    """pymysql.connect 를 가짜로 바꾸고, 만들어진 연결 목록을 돌려준다."""
    created = []

    def fake_connect(**kwargs):
        conn = FakeConnection(**kwargs)
        created.append(conn)
        return conn

    monkeypatch.setattr(db_module.pymysql, "connect", fake_connect)
    return created


# ------------------------------------------------------------------------------
# connect
# ------------------------------------------------------------------------------
def test_connect_returns_self_and_opens_one(opened):
    db = Database()
    assert db.connect(FAKE_SETTINGS) is db
    assert len(opened) == 1
    options = opened[0].kwargs
    # auth.py 등이 기대하는 접속 규칙
    assert options["autocommit"] is True
    assert options["cursorclass"] is pymysql.cursors.DictCursor
    assert options["charset"] == "utf8mb4"
    assert "+09:00" in options["init_command"]
    assert options["ssl"] is None            # 인증서가 없으면 SSL 없이 (로컬 백업용)


def test_connect_uses_ssl_ca(opened, tmp_path):
    ca = tmp_path / "ca.pem"
    settings = SimpleNamespace(**{**vars(FAKE_SETTINGS), "db_ssl_ca": ca})
    Database().connect(settings)
    assert opened[0].kwargs["ssl"] == {"ca": str(ca)}


def test_use_before_connect_fails():
    with pytest.raises(DatabaseError, match="connect"):
        Database().fetch("SELECT 1", ())


@pytest.mark.parametrize("code, word", [(2003, "Aiven"), (1045, "DB_PASSWORD"), (1049, "DB_NAME")])
def test_connect_error_has_hint(monkeypatch, code, word):
    def fail(**kwargs):
        raise pymysql.OperationalError(code, "boom")

    monkeypatch.setattr(db_module.pymysql, "connect", fail)
    with pytest.raises(DatabaseError, match=word):
        Database().connect(FAKE_SETTINGS)


def test_connect_error_hides_password(monkeypatch):
    def fail(**kwargs):
        raise pymysql.OperationalError(1045, "Access denied for user 'tester'")

    monkeypatch.setattr(db_module.pymysql, "connect", fail)
    with pytest.raises(DatabaseError) as info:
        Database().connect(FAKE_SETTINGS)
    assert "test-only" not in str(info.value)


# ------------------------------------------------------------------------------
# execute
# ------------------------------------------------------------------------------
def test_execute_returns_new_id_or_affected(opened):
    db = Database().connect(FAKE_SETTINGS)
    conn = opened[0]
    conn.lastrowid = 42
    assert db.execute("INSERT INTO chat_logs (question) VALUES (%s)", ("q",)) == 42
    conn.lastrowid, conn.affected = 0, 3
    assert db.execute("DELETE FROM sessions WHERE expires_at <= NOW()", ()) == 3


def test_execute_turns_dict_list_into_json(opened):
    db = Database().connect(FAKE_SETTINGS)
    db.execute("INSERT INTO chat_logs (kb_ids, latency_ms, question) VALUES (%s, %s, %s)",
               (["TS-A-T01-01"], {"search": 120}, "벨트가 미끄러져요"))
    _, params = opened[0].executed[-1]
    assert json.loads(params[0]) == ["TS-A-T01-01"]
    assert json.loads(params[1]) == {"search": 120}
    assert params[2] == "벨트가 미끄러져요"        # 일반 값은 그대로


def test_execute_keeps_tuple_for_in(opened):
    db = Database().connect(FAKE_SETTINGS)
    db.execute("DELETE FROM documents WHERE doc_id IN %s", (("a", "b"),))
    assert opened[0].executed[-1][1] == (("a", "b"),)


# ------------------------------------------------------------------------------
# fetch
# ------------------------------------------------------------------------------
def test_fetch_returns_dicts_and_parses_json(opened):
    db = Database().connect(FAKE_SETTINGS)
    conn = opened[0]
    conn.rows = [{"id": 1, "answer": '{"summary": "요약"}'}]
    conn.description = (("id", FIELD_TYPE.LONGLONG), ("answer", FIELD_TYPE.JSON))
    rows = db.fetch("SELECT id, answer FROM chat_history WHERE employee_id = %s", ("A001",))
    assert rows == [{"id": 1, "answer": {"summary": "요약"}}]


def test_fetch_keeps_null_json(opened):
    db = Database().connect(FAKE_SETTINGS)
    conn = opened[0]
    conn.rows = [{"kb_ids": None}]
    conn.description = (("kb_ids", FIELD_TYPE.JSON),)
    assert db.fetch("SELECT kb_ids FROM chat_logs", ()) == [{"kb_ids": None}]


# ------------------------------------------------------------------------------
# 연결 풀
# ------------------------------------------------------------------------------
def test_pool_reuses_connection(opened):
    db = Database().connect(FAKE_SETTINGS)
    for _ in range(5):
        db.fetch("SELECT 1", ())
    assert len(opened) == 1          # 한 번에 하나씩 쓰면 연결 1개를 계속 다시 쓴다


def test_pool_never_exceeds_size(opened, monkeypatch):
    monkeypatch.setattr(db_module, "POOL_WAIT_SECONDS", 2)
    db = Database().connect(FAKE_SETTINGS)
    inside = threading.Barrier(db_module.POOL_SIZE)

    def busy():
        with db._cursor():
            inside.wait(timeout=2)   # 모두 동시에 연결을 빌린 상태를 만든다

    threads = [threading.Thread(target=busy) for _ in range(db_module.POOL_SIZE)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(opened) == db_module.POOL_SIZE

    # 풀이 가득 찬 뒤에도 더 만들지 않고 쉬는 연결을 다시 쓴다
    db.fetch("SELECT 1", ())
    assert len(opened) == db_module.POOL_SIZE


def test_broken_connection_is_replaced(opened):
    db = Database().connect(FAKE_SETTINGS)
    first = opened[0]
    first.fail_with = pymysql.OperationalError(2013, "Lost connection")
    with pytest.raises(pymysql.OperationalError):
        db.fetch("SELECT 1", ())
    assert first.closed                       # 망가진 연결은 버린다
    db.fetch("SELECT 1", ())
    assert len(opened) == 2                   # 다음 요청은 새 연결로


def test_sql_error_keeps_connection(opened):
    db = Database().connect(FAKE_SETTINGS)
    opened[0].fail_with = pymysql.ProgrammingError(1064, "syntax error")
    with pytest.raises(pymysql.ProgrammingError):
        db.fetch("SELEC 1", ())
    opened[0].fail_with = None
    db.fetch("SELECT 1", ())
    assert len(opened) == 1                   # SQL 오류는 연결 문제가 아니므로 그대로 다시 쓴다


def test_dead_connection_on_ping_gives_hint(opened):
    db = Database().connect(FAKE_SETTINGS)
    opened[0].ping_error = pymysql.OperationalError(2003, "Can't connect")
    with pytest.raises(DatabaseError, match="Aiven"):
        db.fetch("SELECT 1", ())
    assert opened[0].closed


def test_close_closes_idle_connections(opened):
    db = Database().connect(FAKE_SETTINGS)
    db.close()
    assert opened[0].closed
