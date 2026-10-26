"""사용자별 대화 상태 (gr.State에 보관, 전역 변수 사용 금지)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Model, User


class ConversationState:
    """ConversationState (기능명세서 기준 뼈대)."""

    def set_equipment(self, state: dict, model: Model):
        """
        명판으로 인식한 설비(모델, 문서 id)를 대화 상태에 저장

        Returns: dict
        """
        raise NotImplementedError("ConversationState.set_equipment")

    def set_user(self, state: dict, user: User):
        """
        로그인에 성공한 사원 정보(사원번호, 이름, 권한)를 대화 상태(gr.State, 서버 메모리)에 저장. 권한은 DB에서 읽은 값만 사용. 대화
        기록 구분에 사용하며, 대화 초기화 시에도 유지

        Returns: dict
        """
        raise NotImplementedError("ConversationState.set_user")

    def clear(self, state: dict):
        """
        대화 초기화 버튼 처리: 설비 필터를 비우고 화면의 대화 내용을 지워 전체 매뉴얼 검색으로 되돌림 (reset_conversation에서 호출)

        Returns: dict
        """
        raise NotImplementedError("ConversationState.clear")

    def save_history(self, state: dict, turn: dict):
        """
        질문·답변 한 쌍을 화면의 대화 기록(로그창)에 추가하고, MySQL에 사원번호별로 저장. 로그아웃 후 다시 로그인해도 볼 수 있음

        Returns: dict
        """
        raise NotImplementedError("ConversationState.save_history")

    def clear_user(self, state: dict):
        """
        로그아웃 처리: 사원 정보와 관리자 모드를 비우고 설비 필터·화면의 대화 기록을 초기화 (clear와 달리 사원 정보까지 제거). DB에 저장된
        대화 기록은 유지

        Returns: dict
        """
        raise NotImplementedError("ConversationState.clear_user")

    def load_history(self, employee_id: str, limit: int):
        """
        로그인한 사원의 과거 대화 기록을 MySQL에서 최신순으로 불러옴 (로그창 출력용)

        Returns: list[dict]
        """
        raise NotImplementedError("ConversationState.load_history")
