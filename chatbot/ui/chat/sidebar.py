"""채팅 화면 왼쪽 사이드바 (Figma 01 대화 기록창: 제목·대화 초기화·대화 기록·사용자 정보·로그아웃, 스타일: sidebar.css)"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr

if TYPE_CHECKING:
    from chatbot.core.schemas import User

SIDEBAR_CSS_PATH = Path(__file__).parent / "sidebar.css"

BRAND = "Wanna Know"


@dataclass
class Sidebar:
    """build_sidebar 가 만든 화면 요소 (이벤트를 연결할 때 씀)"""

    root: gr.Column
    reset_button: gr.Button
    history: gr.HTML
    user_info: gr.HTML
    logout_button: gr.Button


def build_sidebar() -> Sidebar:
    """사이드바 구성: 제목, 대화 초기화 버튼, 대화 기록 목록, (아래쪽) 사원 정보와 로그아웃 버튼"""
    with gr.Column(scale=0, min_width=260, elem_id="chat-sidebar") as root:
        gr.HTML(f"<span class='sb-brand'>{BRAND}</span>")
        reset_button = gr.Button("대화 초기화", elem_classes=["sb-button"])
        gr.HTML("<span class='sb-label'>대화 기록</span>")
        history = gr.HTML("", elem_id="sb-history")
        with gr.Row(elem_id="sb-footer"):
            user_info = gr.HTML("", elem_id="sb-user")
            logout_button = gr.Button("로그아웃", elem_classes=["sb-button", "sb-logout"])
    return Sidebar(root, reset_button, history, user_info, logout_button)


def render_user_info(user: User | None) -> str:
    """사이드바 아래쪽의 사원 정보(이름·사원번호) HTML. 부서는 users 테이블에 없어서 표시하지 않음"""
    if user is None:
        return ""
    return (
        "<div class='sb-user'>"
        f"<span class='sb-user-name'>{escape(user.name)}</span>"
        f"<span class='sb-user-id'>{escape(user.employee_id)}</span>"
        "</div>"
    )


def render_history(rows: list[dict]) -> str:
    """과거 대화 기록(load_history 결과)을 사이드바 목록 HTML 로 바꿈. 질문 원문은 escape 해서 넣음"""
    if not rows:
        return "<p class='sb-empty'>아직 대화 기록이 없습니다.</p>"
    items = "".join(
        "<li>"
        f"<span class='history-question'>{escape(str(r.get('question', '')))}</span>"
        f"<span class='history-time'>{escape(str(r.get('created_at', ''))[:16])}</span>"
        "</li>"
        for r in rows
    )
    return f"<ul class='history-list'>{items}</ul>"
