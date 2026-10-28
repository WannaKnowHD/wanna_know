"""사용자별 대화 상태 (gr.State에 보관, 전역 변수 사용 금지)

state 는 Gradio 가 사용자마다 따로 들고 있는 dict 하나다. 들어 있는 칸:
    user        : User | None   로그인한 사원 (set_user). 대화 초기화해도 남음
    admin_mode  : bool          관리자 대시보드 모드 (ChatApp.change_admin_mode 가 바꿈)
    equipment   : dict | None   명판으로 정한 설비 {"model_name", "aliases", "doc_ids"} (set_equipment)
                                → pipeline.understand_question · Retriever.search(equipment=...) 가 그대로 읽음

화면에 보이는 채팅 내용은 Gradio 채팅창(gr.Chatbot)이 따로 들고 있다 (on_submit 의 history 인자).
그래서 state 에는 넣지 않고, 화면은 ChatApp 이 채팅창에 [] 를 돌려줘서 지운다.
대화 기록은 MySQL chat_history 에 저장(save_history)하고, 로그창은 load_history 로 불러온다.
대화 초기화·로그아웃해도 DB 기록은 남는다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Model, User
    from chatbot.db.db import Database

MAX_HISTORY = 100   # load_history 한 번에 불러오는 최대 개수


def new_state() -> dict:
    """처음 접속했을 때(로그인 전)의 빈 대화 상태"""
    return {"user": None, "admin_mode": False, "equipment": None}


class ConversationState:
    """ConversationState (기능명세서 기준)

    받는 것:
      db : chatbot.db.db.Database (대화 기록 저장·조회). None 이면 저장·조회를 건너뜀
    """

    def __init__(self, db: Database | None = None):
        self._db = db

    def set_equipment(self, state: dict, model: Model):
        """
        명판으로 인식한 설비(모델, 문서 id)를 대화 상태에 저장

        Returns: dict
        """
        state = {**new_state(), **(state or {})}
        state["equipment"] = {
            "model_name": model.model_name,
            "aliases": list(model.aliases),
            "doc_ids": list(model.doc_ids),
        }
        return state

    def set_user(self, state: dict, user: User):
        """
        로그인에 성공한 사원 정보(사원번호, 이름, 권한)를 대화 상태(gr.State, 서버 메모리)에 저장. 권한은 DB에서 읽은 값만 사용. 대화
        기록 구분에 사용하며, 대화 초기화 시에도 유지

        Returns: dict
        """
        state = {**new_state(), **(state or {})}
        state["user"] = user              # User 는 값을 바꿀 수 없는 객체라 그대로 넣어도 안전
        state["admin_mode"] = False       # 로그인 직후에는 항상 채팅 화면
        return state

    def clear(self, state: dict):
        """
        대화 초기화 버튼 처리: 설비 필터를 비우고 화면의 대화 내용을 지워 전체 매뉴얼 검색으로 되돌림 (reset_conversation에서 호출)

        Returns: dict
        """
        state = {**new_state(), **(state or {})}
        state["equipment"] = None
        return state                      # user · admin_mode 는 그대로. 화면 채팅은 ChatApp 이 지움

    def save_history(self, state: dict, turn: dict):
        """
        질문·답변 한 쌍을 화면의 대화 기록(로그창)에 추가하고, MySQL에 사원번호별로 저장. 로그아웃 후 다시 로그인해도 볼 수 있음
        (화면 채팅창은 Gradio 가 들고 있으므로 여기서는 MySQL chat_history 에만 저장)

        turn = {"question": str, "answer": dict (응답 JSON)}
        Returns: dict
        """
        state = {**new_state(), **(state or {})}
        question = (turn.get("question") or "").strip()
        answer = turn.get("answer") or {}
        user = state.get("user")
        if not question or user is None:  # 빈 질문·로그인 전에는 저장하지 않음
            return state
        if self._db is not None:
            try:
                self._db.execute(
                    "INSERT INTO chat_history (employee_id, question, answer) VALUES (%s, %s, %s)",
                    (user.employee_id, question, answer),
                )
            except Exception as e:  # noqa: BLE001 — DB 저장이 실패해도 채팅은 계속. 사원번호는 출력하지 않음
                print(f"[state] 대화 기록 저장 실패: {type(e).__name__}: {e}")
        return state

    def clear_user(self, state: dict):
        """
        로그아웃 처리: 사원 정보와 관리자 모드를 비우고 설비 필터·화면의 대화 기록을 초기화 (clear와 달리 사원 정보까지 제거). DB에 저장된
        대화 기록은 유지

        Returns: dict
        """
        return new_state()

    def load_history(self, employee_id: str, limit: int = 20):
        """
        로그인한 사원의 과거 대화 기록을 MySQL에서 최신순으로 불러옴 (로그창 출력용)

        Returns: list[dict]  [{"created_at", "question", "answer"}, ...]
        """
        employee_id = (employee_id or "").strip()
        if self._db is None or not employee_id:
            return []
        limit = max(1, min(int(limit), MAX_HISTORY))
        try:
            rows = self._db.fetch(
                "SELECT created_at, question, answer FROM chat_history "
                "WHERE employee_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                (employee_id, limit),
            )
        except Exception as e:  # noqa: BLE001 — 조회 실패해도 화면은 계속
            print(f"[state] 대화 기록 조회 실패: {type(e).__name__}: {e}")
            return []
        return [{"created_at": r["created_at"], "question": r["question"], "answer": r["answer"]} for r in rows]
