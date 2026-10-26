"""로그인, 세션 관리"""
from __future__ import annotations

import secrets
from functools import cache
from typing import TYPE_CHECKING

import bcrypt

from chatbot.core.schemas import User

if TYPE_CHECKING:
    from chatbot.db.db import Database


@cache
def _dummy_hash() -> bytes:
    """없는 사원번호도 비밀번호 검사와 같은 시간이 걸리게 하려고 쓰는 가짜 해시."""
    return bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())


class AuthManager:
    """AuthManager (기능명세서 기준 뼈대)."""

    def __init__(self, db: Database | None = None, session_hours: int = 8):
        self._db = db
        self._session_hours = session_hours

    @staticmethod
    def _to_user(row: dict) -> User:
        return User(employee_id=row["employee_id"], name=row["name"], role=row["role"])

    def _require_db(self) -> Database:
        if self._db is None:
            raise RuntimeError("AuthManager.load_users(db)를 먼저 호출해야 합니다")
        return self._db

    def load_users(self, db: Database):
        """
        MySQL users 테이블(사원번호, 비밀번호 해시, 이름, 권한)에서 사원 계정을 읽음. 계정은 scripts/seed.py로 미리 넣어 둠.
        비밀번호는 해시로만 보관하고 원문은 저장하지 않음

        Returns: dict[str, User]
        """
        self._db = db
        rows = db.fetch("SELECT employee_id, name, role FROM users", ())
        return {row["employee_id"]: self._to_user(row) for row in rows}

    def authenticate(self, employee_id: str, password: str):
        """
        로그인 시 사원번호·비밀번호를 사원 계정과 대조. 일치 → 사원 정보 반환, 사원번호 없음·비밀번호 불일치 → None

        Returns: User | None
        """
        db = self._require_db()
        employee_id = (employee_id or "").strip()
        rows = db.fetch(
            "SELECT employee_id, password_hash, name, role FROM users WHERE employee_id = %s", (employee_id,)
        ) if employee_id else []
        row = rows[0] if rows else None
        stored = row["password_hash"].encode() if row else _dummy_hash()
        try:
            matched = bcrypt.checkpw((password or "").encode(), stored)
        except ValueError:  # bcrypt가 받을 수 없는 입력(72바이트 초과 등)
            matched = False
        return self._to_user(row) if row and matched else None

    def create_session(self, user: User):
        """
        로그인 성공 시 무작위 토큰(secrets.token_hex)을 만들어 MySQL sessions 테이블에 사원번호·생성 시각·만료 시각(8시간
        후)과 함께 저장

        Returns: str (token)
        """
        db = self._require_db()
        token = secrets.token_hex(32)  # 64자, sessions.token CHAR(64)
        db.execute("DELETE FROM sessions WHERE expires_at <= NOW()", ())  # 만료된 세션 정리
        db.execute(
            "INSERT INTO sessions (token, employee_id, created_at, expires_at) "
            "VALUES (%s, %s, NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
            (token, user.employee_id, self._session_hours),
        )
        return token

    def restore_session(self, token: str | None):
        """
        브라우저에 저장된 토큰으로 sessions 테이블을 조회. 만료 전이면 users 테이블에서 최신 권한을 읽어 User를 다시 만들고, 없거나
        만료면 None

        Returns: User | None
        """
        if not token:
            return None
        rows = self._require_db().fetch(
            "SELECT u.employee_id, u.name, u.role FROM sessions s "
            "JOIN users u ON u.employee_id = s.employee_id "
            "WHERE s.token = %s AND s.expires_at > NOW()",
            (token,),
        )
        return self._to_user(rows[0]) if rows else None

    def delete_session(self, token: str):
        """
        로그아웃 시 sessions 테이블에서 토큰을 삭제

        Returns: None
        """
        if token:
            self._require_db().execute("DELETE FROM sessions WHERE token = %s", (token,))
