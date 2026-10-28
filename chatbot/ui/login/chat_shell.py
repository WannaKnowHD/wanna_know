"""로그인 뒤 채팅 화면 틀 (사원 정보·대화 기록·관리자 전환·로그아웃, 스타일: chat_shell.css)

질문 입력·답변 출력은 on_submit 등을 구현할 때 이 틀의 채팅 영역(chat-main)에 붙인다.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr

if TYPE_CHECKING:
    from chatbot.core.schemas import User

CHAT_SHELL_CSS_PATH = Path(__file__).parent / "chat_shell.css"


@dataclass
class ChatShell:
    """build_chat_shell 이 만든 화면 요소 (이벤트를 연결할 때 씀)"""

    root: gr.Column
    user_info: gr.HTML
    admin_button: gr.Button
    theme_button: gr.Button
    logout_button: gr.Button
    history: gr.HTML


def build_chat_shell() -> ChatShell:
    """채팅 화면 틀 구성. 처음에는 숨겨 두고, 로그인하면 ChatApp 이 보여 준다."""
    with gr.Column(visible=False, elem_id="chat-root") as root:
        with gr.Row(elem_id="chat-header", equal_height=True):
            gr.HTML("<span class='chat-brand'>Wanna Know</span>", elem_classes=["chat-brand-box"])
            user_info = gr.HTML("", elem_classes=["chat-user-box"])
            admin_button = gr.Button("관리자 전환", visible=False, elem_classes=["chat-header-button"])
            theme_button = gr.Button("라이트 / 다크 전환", elem_classes=["chat-header-button", "theme-toggle"])
            logout_button = gr.Button("로그아웃", elem_classes=["chat-header-button"])
        with gr.Row(elem_id="chat-body"):
            with gr.Column(scale=1, min_width=240, elem_id="chat-history"):
                gr.HTML("<h3 class='chat-history-title'>대화 기록</h3>")
                history = gr.HTML("")
            with gr.Column(scale=4, elem_id="chat-main"):
                gr.HTML("<p class='chat-placeholder'>질문 입력과 답변 출력은 다음 단계에서 연결됩니다.</p>")
    return ChatShell(root, user_info, admin_button, theme_button, logout_button, history)


def render_user_info(user: User | None) -> str:
    """채팅 화면 상단의 사원 정보(이름·사원번호) HTML. 부서는 users 테이블에 없어서 표시하지 않음"""
    if user is None:
        return ""
    return (
        "<div class='chat-user'>"
        f"<span class='chat-user-name'>{escape(user.name)}</span>"
        f"<span class='chat-user-id'>{escape(user.employee_id)}</span>"
        "</div>"
    )


def render_history(rows: list[dict]) -> str:
    """과거 대화 기록(load_history 결과)을 로그창용 HTML 목록으로 바꿈. 질문 원문은 escape 해서 넣음"""
    if not rows:
        return "<p class='history-empty'>아직 대화 기록이 없습니다.</p>"
    items = "".join(
        f"<li><span class='history-time'>{escape(str(r.get('created_at', ''))[:16])}</span>"
        f"<span class='history-question'>{escape(str(r.get('question', '')))}</span></li>"
        for r in rows
    )
    return f"<ul class='history-list'>{items}</ul>"
