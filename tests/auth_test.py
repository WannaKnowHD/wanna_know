"""auth.py 동작 확인 (로컬 전용, .gitignore 대상). DB 없이 메모리 가짜 DB로 검사함.

실행 (프로젝트 루트에서):
    python tests/auth_test.py          # 결과를 항목별로 출력
    pytest tests/auth_test.py -v       # pytest로 실행
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import secrets
import bcrypt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 프로젝트 루트에서 chatbot 패키지 찾기

from chatbot.core.auth import AuthManager
from chatbot.core.schemas import User


class FakeDb:
    """AuthManager가 보내는 SQL만 알아듣는 가짜 Database. 시연 값이 아닌 테스트 전용 계정."""

    def __init__(self):
        def hashed(pw):  # seed.py 와 같은 형식 (테스트가 빠르도록 반복 횟수만 1000)
            salt = secrets.token_bytes(16)
            digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 1000)
            return f"pbkdf2_sha256$1000${salt.hex()}${digest.hex()}"

        self.users = {
            "e1": {"employee_id": "e1", "password_hash": hashed("pw-eng"), "name": "엔지니어", "role": "engineer"},
            "a1": {"employee_id": "a1", "password_hash": hashed("pw-admin"), "name": "관리자", "role": "admin"},
        }
        self.sessions = {}  # token → (employee_id, expires_at)
        self.broken = False

    def _check(self):
        if self.broken:
            raise ConnectionError("db down")

    def fetch(self, sql, params=()):
        self._check()
        if "FROM sessions" in sql:
            emp, exp = self.sessions.get(params[0], (None, None))
            if emp and exp > datetime.now() and emp in self.users:
                return [{k: self.users[emp][k] for k in ("employee_id", "name", "role")}]
            return []
        if "WHERE employee_id" in sql:
            return [self.users[params[0]]] if params[0] in self.users else []
        return list(self.users.values())

    def execute(self, sql, params=()):
        self._check()
        if sql.startswith("INSERT INTO sessions"):
            self.sessions[params[0]] = (params[1], datetime.now() + timedelta(hours=params[2]))
        elif sql.startswith("DELETE FROM sessions WHERE token"):
            self.sessions.pop(params[0], None)
        elif sql.startswith("DELETE FROM sessions WHERE expires_at"):
            self.sessions = {t: v for t, v in self.sessions.items() if v[1] > datetime.now()}
        return 1


def make_auth():
    db = FakeDb()
    auth = AuthManager(session_hours=8)
    auth.load_users(db)
    return auth, db


def test_load_users():
    auth, db = make_auth()
    users = auth.load_users(db)
    assert set(users) == {"e1", "a1"}
    assert users["a1"].is_admin and not users["e1"].is_admin
    assert not hasattr(users["e1"], "password_hash")  # User에는 해시를 넣지 않음


def test_authenticate_success():
    auth, _ = make_auth()
    assert auth.authenticate("e1", "pw-eng") == User("e1", "엔지니어", "engineer")
    assert auth.authenticate("a1", "pw-admin").is_admin
    assert auth.authenticate(" e1 ", "pw-eng").employee_id == "e1"  # 사원번호 앞뒤 공백은 무시


def test_authenticate_failure():
    auth, _ = make_auth()
    assert auth.authenticate("e1", "wrong") is None      # 비밀번호 불일치
    assert auth.authenticate("nobody", "pw-eng") is None  # 없는 사원번호
    assert auth.authenticate("", "pw-eng") is None
    assert auth.authenticate("e1", "") is None
    assert auth.authenticate("e1", "x" * 100) is None     # bcrypt 72바이트 초과 입력도 예외 없이 거부


def test_create_session():
    auth, db = make_auth()
    user = auth.authenticate("e1", "pw-eng")
    token = auth.create_session(user)
    assert len(token) == 64 and int(token, 16) >= 0       # 16진수 64자 (sessions.token CHAR(64))
    emp, expires = db.sessions[token]
    assert emp == "e1"
    assert timedelta(hours=7, minutes=59) < expires - datetime.now() <= timedelta(hours=8)
    assert auth.create_session(user) != token             # 매번 다른 토큰


def test_restore_session():
    auth, db = make_auth()
    user = auth.authenticate("e1", "pw-eng")
    token = auth.create_session(user)
    assert auth.restore_session(token) == user
    assert auth.restore_session(None) is None
    assert auth.restore_session("") is None
    assert auth.restore_session("없는-토큰") is None


def test_restore_session_uses_latest_role():
    auth, db = make_auth()
    token = auth.create_session(auth.authenticate("e1", "pw-eng"))
    db.users["e1"]["role"] = "admin"                       # 로그인 후 DB에서 권한이 바뀜
    assert auth.restore_session(token).is_admin


def test_expired_session():
    auth, db = make_auth()
    token = auth.create_session(auth.authenticate("e1", "pw-eng"))
    db.sessions[token] = ("e1", datetime.now() - timedelta(seconds=1))
    assert auth.restore_session(token) is None
    auth.create_session(auth.authenticate("a1", "pw-admin"))  # 새 세션을 만들 때 만료 세션 정리
    assert token not in db.sessions


def test_delete_session():
    auth, db = make_auth()
    token = auth.create_session(auth.authenticate("e1", "pw-eng"))
    auth.delete_session(token)
    assert token not in db.sessions and auth.restore_session(token) is None
    auth.delete_session(None)  # 토큰이 없어도 예외 없음
    auth.delete_session("없는-토큰")


def test_requires_db():
    try:
        AuthManager().authenticate("e1", "pw-eng")
    except RuntimeError:
        return
    raise AssertionError("db 없이 호출하면 RuntimeError가 나야 함")


def test_db_error_propagates():
    auth, db = make_auth()
    db.broken = True  # DB 오류는 삼키지 않고 위로 올림 (화면이 안내 문구를 띄움)
    for call in (lambda: auth.authenticate("e1", "pw-eng"), lambda: auth.restore_session("t"),
                 lambda: auth.delete_session("t")):
        try:
            call()
        except ConnectionError:
            continue
        raise AssertionError("DB 오류가 전달되어야 함")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
