"""MySQL(Aiven) 연결·실행"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.config import Settings


class Database:
    """Database (기능명세서 기준 뼈대)."""

    def connect(self, settings: Settings):
        """
        MySQL(Aiven)에 SSL(ca.pem)로 연결하고 연결 풀을 만듦. 앱 시작 시 1번 호출

        Returns: Database
        """
        raise NotImplementedError("Database.connect")

    def execute(self, sql: str, params: tuple):
        """
        INSERT·UPDATE·DELETE 실행 (로그 저장, 대화 기록 저장, 담당자 확인 갱신 등). 값은 매개변수로 넘겨 SQL 인젝션 방지

        Returns: int (영향받은 행 수 또는 새 id)
        """
        raise NotImplementedError("Database.execute")

    def fetch(self, sql: str, params: tuple):
        """
        SELECT 실행 후 결과를 dict 목록으로 반환 (계정 조회, 로그 조회, 대화 기록 조회)

        Returns: list[dict]
        """
        raise NotImplementedError("Database.fetch")
