"""로그인, 세션 관리"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import User
    from chatbot.db.db import Database


class AuthManager:
    """AuthManager (기능명세서 기준 뼈대)."""

    def load_users(self, db: Database):
        """
        MySQL users 테이블(사원번호, 비밀번호 해시, 이름, 권한)에서 사원 계정을 읽음. 계정은 scripts/seed.py로 미리 넣어 둠.
        비밀번호는 해시로만 보관하고 원문은 저장하지 않음

        Returns: dict[str, User]
        """
        raise NotImplementedError("AuthManager.load_users")

    def authenticate(self, employee_id: str, password: str):
        """
        로그인 시 사원번호·비밀번호를 사원 계정과 대조. 일치 → 사원 정보 반환, 사원번호 없음·비밀번호 불일치 → None

        Returns: User | None
        """
        raise NotImplementedError("AuthManager.authenticate")

    def create_session(self, user: User):
        """
        로그인 성공 시 무작위 토큰(secrets.token_hex)을 만들어 MySQL sessions 테이블에 사원번호·생성 시각·만료 시각(8시간
        후)과 함께 저장

        Returns: str (token)
        """
        raise NotImplementedError("AuthManager.create_session")

    def restore_session(self, token: str | None):
        """
        브라우저에 저장된 토큰으로 sessions 테이블을 조회. 만료 전이면 users 테이블에서 최신 권한을 읽어 User를 다시 만들고, 없거나
        만료면 None

        Returns: User | None
        """
        raise NotImplementedError("AuthManager.restore_session")

    def delete_session(self, token: str):
        """
        로그아웃 시 sessions 테이블에서 토큰을 삭제

        Returns: None
        """
        raise NotImplementedError("AuthManager.delete_session")
