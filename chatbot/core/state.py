"""사용자별 대화 상태 (gr.State에 보관, 전역 변수 사용 금지)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Model, User
    from chatbot.db.db import Database

# state dict 의 키 (gr.State 한 칸 = 브라우저 탭 하나의 상태)
#   user       : User 로그인한 사원
#   admin_mode : bool 관리자 모드 여부
#   equipment  : Model 명판으로 정한 설비 (설비 필터)
#   context    : dict 직전 질문의 설비·부품 맥락 (후속 질문용)
#   history    : list[dict] 화면의 대화 내용 [{"question": ..., "answer": ...}]
_SCREEN_KEYS = ("equipment", "context", "history")   # 대화 초기화로 비우는 값
_USER_KEYS = ("user", "admin_mode")                  # 로그아웃으로 비우는 값 (+ 위 대화 값)

HISTORY_LIMIT = 50   # 로그인할 때 불러오는 과거 대화 수


class ConversationState:
    """ConversationState (기능명세서 기준 뼈대)."""

    def __init__(self, db: Database | None = None):
        self._db = db   # save_history · load_history 가 chat_history 테이블을 쓸 때 필요

    def set_equipment(self, state: dict, model: Model):
        """
        명판으로 인식한 설비(모델, 문서 id)를 대화 상태에 저장

        Returns: dict
        """
        state["equipment"] = model
        return state

    def set_user(self, state: dict, user: User):
        """
        로그인에 성공한 사원 정보(사원번호, 이름, 권한)를 대화 상태(gr.State, 서버 메모리)에 저장. 권한은 DB에서 읽은 값만 사용. 대화
        기록 구분에 사용하며, 대화 초기화 시에도 유지

        Returns: dict
        """
        state["user"] = user
        state["admin_mode"] = False   # 관리자 모드는 로그인한 직후에는 항상 꺼져 있음 (전환 버튼으로만 켬)
        return state

    def clear(self, state: dict):
        """
        대화 초기화 버튼 처리: 설비 필터를 비우고 화면의 대화 내용을 지워 전체 매뉴얼 검색으로 되돌림 (reset_conversation에서 호출)

        Returns: dict
        """
        for key in _SCREEN_KEYS:
            state.pop(key, None)
        return state

    def save_history(self, state: dict, turn: dict):
        """
        질문·답변 한 쌍을 화면의 대화 기록(로그창)에 추가하고, MySQL에 사원번호별로 저장. 로그아웃 후 다시 로그인해도 볼 수 있음

        Returns: dict
        """
        state.setdefault("history", []).append(turn)
        user = state.get("user")
        if self._db is not None and user is not None:
            try:
                self._db.execute(
                    "INSERT INTO chat_history (employee_id, question, answer) VALUES (%s, %s, %s)",
                    (user.employee_id, turn["question"], turn["answer"]),   # answer(dict)는 db.py 가 JSON 으로 바꿔 저장
                )
            except Exception as e:  # DB 오류(pymysql 등)로 저장에 실패해도 답변은 화면에 보여야 함
                print(f"[state] 대화 기록 저장 실패: {type(e).__name__}: {e}")
        return state

    def clear_user(self, state: dict):
        """
        로그아웃 처리: 사원 정보와 관리자 모드를 비우고 설비 필터·화면의 대화 기록을 초기화 (clear와 달리 사원 정보까지 제거). DB에 저장된
        대화 기록은 유지

        Returns: dict
        """
        for key in _USER_KEYS + _SCREEN_KEYS:
            state.pop(key, None)
        return state

    def load_history(self, employee_id: str, limit: int = HISTORY_LIMIT):
        """
        로그인한 사원의 과거 대화 기록을 MySQL에서 최신순으로 불러옴 (로그창 출력용)

        Returns: list[dict]
        """
        if self._db is None or not employee_id:
            return []
        # answer(JSON 칸)는 db.py 의 fetch 가 dict 로 바꿔 줌
        return self._db.fetch(
            "SELECT created_at, question, answer FROM chat_history "
            "WHERE employee_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (employee_id, int(limit)),
        )
