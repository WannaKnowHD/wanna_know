"""
==============================================================================
 db.py : MySQL(Aiven) 연결·실행  (담당: 이정빈 / WBS 3.1.4)
==============================================================================

[이 파일이 하는 일]
  앱 전체가 같이 쓰는 DB 창구. 연결 풀을 만들어 두고 SQL 을 실행해 준다.
    - connect : SSL(ca.pem)로 접속하고 연결 풀을 만든다. 앱 시작 시 1번
    - execute : INSERT·UPDATE·DELETE (바로 저장된다)
    - fetch   : SELECT → dict 목록

[규칙]
  - 값은 항상 params 로 넘긴다. SQL 문자열에 값을 직접 이어 붙이지 않는다. (SQL 인젝션 방지)
      db.fetch("SELECT * FROM users WHERE employee_id = %s", (employee_id,))
  - JSON 칸(kb_ids, latency_ms, answer 등)에는 dict·list 를 그대로 넘기면 된다.
    저장할 때 JSON 글자로 바꾸고, fetch 로 읽을 때 다시 dict·list 로 돌려준다.
  - IN 조건에는 tuple 을 넘긴다. (list 는 JSON 으로 바뀌므로)
      db.fetch("SELECT * FROM documents WHERE doc_id IN %s", (("a", "b"),))
  - 시간은 한국 시간(+09:00) 기준이다. NOW()·CURRENT_TIMESTAMP 모두 한국 시간으로 저장된다.

[사용 예]
    from chatbot.core.config import Settings
    from chatbot.db.db import Database

    db = Database().connect(Settings.load())
    rows = db.fetch("SELECT employee_id, name FROM users", ())
==============================================================================
"""
from __future__ import annotations

import json
import queue
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

import pymysql
from pymysql.constants import FIELD_TYPE
from pymysql.cursors import DictCursor

if TYPE_CHECKING:
    from chatbot.core.config import Settings


# Gradio 는 여러 사용자의 요청을 동시에 처리한다. pymysql 연결 하나를 여럿이 같이 쓰면 깨지므로
# 연결을 여러 개 만들어 두고 요청마다 하나씩 빌려 쓴다(연결 풀).
POOL_SIZE = 5             # 동시에 열어 둘 최대 연결 수
POOL_WAIT_SECONDS = 10    # 연결이 모두 사용 중일 때 기다리는 최대 시간
CONNECT_TIMEOUT = 10      # 10초 안에 접속 못 하면 포기 (Aiven 서비스 꺼짐 등)
QUERY_TIMEOUT = 30        # SQL 하나가 30초를 넘기면 실패로 본다

# Aiven 서버 시간은 UTC 다. 그대로 두면 대시보드의 "오늘 로그"·세션 만료 시각이 9시간 어긋난다.
TIME_ZONE = "+09:00"

# 접속 실패 오류 번호 → 해결 방법
CONNECT_HINTS: dict[int, str] = {
    2003: "Aiven 서비스가 꺼져 있거나 DB_HOST·DB_PORT 가 틀렸습니다 → Aiven 콘솔에서 서비스가 Running 인지 확인",
    2005: "DB_HOST 주소를 찾을 수 없습니다 → .env 의 DB_HOST 확인",
    1045: "DB_USER·DB_PASSWORD 가 틀렸습니다 → Aiven 콘솔 Connection information 과 비교",
    1049: "DB_NAME 데이터베이스가 없습니다 → .env 의 DB_NAME 확인 (Aiven 기본값: defaultdb)",
}


class DatabaseError(RuntimeError):
    """DB 접속 문제(서버 꺼짐·주소·계정·인증서)나 사용 순서 문제. SQL 문법 오류는 pymysql 오류 그대로 난다."""


class Database:
    """연결 풀을 가진 MySQL 창구. Database().connect(settings) 로 만든다."""

    def __init__(self):
        self._settings: Settings | None = None
        self._pool: queue.LifoQueue | None = None   # 쉬고 있는 연결 (최근에 쓴 것부터 다시 쓴다)
        self._opened = 0                             # 지금까지 열려 있는 연결 수 (빌려 간 것 포함)
        self._lock = threading.Lock()                # _opened 를 여러 요청이 동시에 바꾸지 못하게

    def connect(self, settings: Settings):
        """
        MySQL(Aiven)에 SSL(ca.pem)로 연결하고 연결 풀을 만듦. 앱 시작 시 1번 호출

        받는 것: Settings (DB 접속 정보·인증서 경로)
        돌려주는 것: Database (자기 자신. db = Database().connect(settings) 처럼 쓴다)
        접속 정보가 틀렸으면 앱이 시작될 때 바로 DatabaseError 로 알려 준다.
        """
        self.close()
        self._settings = settings
        self._pool = queue.LifoQueue(maxsize=POOL_SIZE)
        self._opened = 0
        # 연결 하나를 바로 만들어 본다. (첫 질문 때가 아니라 지금 접속 문제를 알기 위해)
        self._release(self._acquire(), broken=False)
        return self

    def execute(self, sql: str, params: tuple = ()):
        """
        INSERT·UPDATE·DELETE 실행 (로그 저장, 대화 기록 저장, 담당자 확인 갱신 등). 값은 매개변수로 넘겨 SQL 인젝션 방지

        받는 것: SQL (값 자리는 %s), 값 tuple
        돌려주는 것: int. AUTO_INCREMENT 행을 INSERT 했으면 새 id(log_id 등), 아니면 영향받은 행 수
        실행하자마자 저장된다(autocommit). 따로 commit 할 필요 없다.
        """
        with self._cursor() as cursor:
            affected = cursor.execute(sql, _to_db_params(params))
            return cursor.lastrowid or affected

    def fetch(self, sql: str, params: tuple = ()):
        """
        SELECT 실행 후 결과를 dict 목록으로 반환 (계정 조회, 로그 조회, 대화 기록 조회)

        받는 것: SQL (값 자리는 %s), 값 tuple
        돌려주는 것: list[dict] (예: [{"employee_id": "A001", "name": "홍길동"}]). JSON 칸은 dict·list 로 바꿔 준다.
        """
        with self._cursor() as cursor:
            cursor.execute(sql, _to_db_params(params))
            rows = list(cursor.fetchall())
            json_columns = [col[0] for col in cursor.description or () if col[1] == FIELD_TYPE.JSON]
        for row in rows:
            for name in json_columns:
                if isinstance(row[name], (str, bytes)):
                    row[name] = json.loads(row[name])
        return rows

    def close(self):
        """
        쉬고 있는 연결을 모두 닫는다. (테스트 끝·앱 종료 시)

        받는 것: 없음
        돌려주는 것: 없음
        """
        while self._pool is not None:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            _close_quietly(conn)
            with self._lock:
                self._opened -= 1

    # --------------------------------------------------------------------------
    # 연결 빌리기·돌려주기 (이 파일 안에서만 쓴다)
    # --------------------------------------------------------------------------
    @contextmanager
    def _cursor(self) -> Iterator[DictCursor]:
        """연결을 하나 빌려 커서를 주고, 끝나면 연결을 풀에 돌려준다."""
        conn = self._acquire()
        broken = False
        try:
            with conn.cursor() as cursor:
                yield cursor
        except (pymysql.OperationalError, pymysql.InterfaceError):
            broken = True   # 연결 자체가 망가진 경우 → 풀에 돌려주지 않고 버린다
            raise
        finally:
            self._release(conn, broken)

    def _acquire(self):
        """쉬고 있는 연결을 꺼내거나, 여유가 있으면 새로 만든다. 꺼낸 연결은 살아 있는지 확인한다."""
        if self._pool is None or self._settings is None:
            raise DatabaseError("Database.connect(settings) 를 먼저 호출해야 합니다 (앱 시작 시 1번)")

        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            conn = None
        if conn is None:
            with self._lock:
                can_open = self._opened < POOL_SIZE
                if can_open:
                    self._opened += 1
            if can_open:
                try:
                    return self._open()
                except Exception:
                    with self._lock:
                        self._opened -= 1
                    raise
            try:
                conn = self._pool.get(timeout=POOL_WAIT_SECONDS)
            except queue.Empty:
                raise DatabaseError(f"DB 연결 {POOL_SIZE}개가 모두 사용 중입니다 ({POOL_WAIT_SECONDS}초 대기 후 포기)") from None

        # Aiven 은 오래 쉰 연결을 끊는다. 그대로 쓰면 "MySQL server has gone away" 가 나므로 확인하고 다시 잇는다.
        try:
            conn.ping(reconnect=True)
        except pymysql.MySQLError as error:
            self._release(conn, broken=True)
            raise DatabaseError(_connect_message(error)) from error
        return conn

    def _release(self, conn, broken: bool):
        """다 쓴 연결을 풀에 돌려준다. 망가진 연결은 닫고 자리를 비운다."""
        if broken or self._pool is None:
            _close_quietly(conn)
            with self._lock:
                self._opened -= 1
            return
        self._pool.put_nowait(conn)

    def _open(self):
        """새 연결을 하나 연다. 실패하면 원인과 해결 방법을 담은 DatabaseError 를 낸다."""
        s = self._settings
        # 인증서 경로가 있으면 SSL 로 접속(Aiven, 인증서로 서버 확인), 없으면 SSL 없이 접속(로컬 MySQL 백업용)
        ssl_options = {"ca": str(s.db_ssl_ca)} if s.db_ssl_ca else None
        try:
            return pymysql.connect(
                host=s.db_host,
                port=s.db_port,
                user=s.db_user,
                password=s.db_password,
                database=s.db_name,
                charset="utf8mb4",              # 한글이 깨지지 않게
                ssl=ssl_options,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=QUERY_TIMEOUT,
                write_timeout=QUERY_TIMEOUT,
                autocommit=True,                # execute 하자마자 저장 (auth.py 등은 commit 을 따로 부르지 않는다)
                cursorclass=DictCursor,         # fetch 결과를 dict 로 (row["name"] 처럼 읽는다)
                init_command=f"SET time_zone = '{TIME_ZONE}'",   # 다시 이을 때도 매번 적용된다
            )
        except pymysql.MySQLError as error:
            raise DatabaseError(_connect_message(error)) from error


# ------------------------------------------------------------------------------
# 도우미 함수
# ------------------------------------------------------------------------------
def _to_db_params(params: tuple) -> tuple:
    """dict·list 값을 JSON 글자로 바꾼다. (pymysql 은 list 를 JSON 이 아닌 (a, b) 꼴로 넣어 버린다)"""
    if params is None:
        return ()
    return tuple(json.dumps(p, ensure_ascii=False) if isinstance(p, (dict, list)) else p for p in params)


def _connect_message(error: pymysql.MySQLError) -> str:
    """접속 오류를 "원인 → 해결 방법" 한 줄로 만든다. (비밀번호는 넣지 않는다)"""
    code = error.args[0] if error.args and isinstance(error.args[0], int) else None
    detail = str(error)
    if "SSL" in detail or "certificate" in detail.lower():
        hint = "SSL 인증서 확인 실패 → certs/ca.pem 이 이 Aiven 서비스의 CA certificate 인지 확인"
    else:
        hint = CONNECT_HINTS.get(code, "DB 접속 실패 → python scripts/test_db.py 로 접속 정보를 점검")
    return f"{hint} (원래 오류: {detail})"


def _close_quietly(conn) -> None:
    """연결을 닫는다. 이미 끊긴 연결이면 나는 오류는 무시한다."""
    try:
        conn.close()
    except Exception:
        pass
