"""로그인 뒤 채팅 화면 전체 (왼쪽 사이드바 + 오른쪽 채팅 영역, 스타일: chat.css)"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gradio as gr

from chatbot.ui.chat.chat_area import ChatArea, build_chat_area
from chatbot.ui.chat.sidebar import Sidebar, build_sidebar

CHAT_CSS_PATH = Path(__file__).parent / "chat.css"


@dataclass
class ChatScreen:
    """build_chat_screen 이 만든 화면 요소 (ChatApp 이 이벤트를 연결할 때 씀)"""

    root: gr.Row
    sidebar: Sidebar
    area: ChatArea


def build_chat_screen() -> ChatScreen:
    """채팅 화면 구성. 처음에는 숨겨 두고, 로그인하면 ChatApp 이 보여 준다."""
    with gr.Row(visible=False, elem_id="chat-root", equal_height=True) as root:
        sidebar = build_sidebar()
        area = build_chat_area()
    return ChatScreen(root, sidebar, area)
