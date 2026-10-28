"""채팅 화면 오른쪽 채팅 영역 (Figma 01~04: 상단 바·메시지·예시 질문·입력 바, 스타일: chat_area.css)"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gradio as gr

from chatbot.ui.chat.messages import render_equipment, render_welcome
from chatbot.ui.login.theme import THEME_TOGGLE_ENABLED

CHAT_AREA_CSS_PATH = Path(__file__).parent / "chat_area.css"

# 예시 질문 템플릿 (Q-06). 임시 문구 — show_examples(3.7.3)가 진짜 목록을 정하면 그쪽에서 가져옴
EXAMPLE_QUESTIONS = (
    "3라인 벨트가 한쪽으로 쏠려요",
    "모터가 뜨거워요",
    "체인이 늘어났어요",
    "알람 코드 E-05",
)
INPUT_PLACEHOLDER = "증상이나 알람 코드를 입력하세요"


@dataclass
class ChatArea:
    """build_chat_area 가 만든 화면 요소 (이벤트를 연결할 때 씀)"""

    root: gr.Column
    equipment: gr.HTML
    admin_button: gr.Button
    theme_button: gr.Button
    chatbot: gr.Chatbot
    examples: gr.Row
    example_buttons: list[gr.Button]
    preview_row: gr.Row
    preview: gr.Image
    preview_remove: gr.Button
    image_button: gr.UploadButton
    message: gr.Textbox
    send_button: gr.Button


def build_chat_area() -> ChatArea:
    """채팅 영역 구성: 현재 설비·관리자 전환·라이트/다크 버튼, 채팅창, 예시 질문, 사진 첨부·질문 입력·전송"""
    with gr.Column(scale=1, elem_id="chat-area") as root:
        with gr.Row(elem_id="chat-topbar", equal_height=True):
            equipment = gr.HTML(render_equipment(None), elem_id="chat-equipment")
            admin_button = gr.Button("관리자 전환", visible=False, elem_classes=["ca-button"])
            theme_button = gr.Button(
                "라이트 / 다크", elem_classes=["ca-button", "theme-toggle"], visible=THEME_TOGGLE_ENABLED
            )

        chatbot = gr.Chatbot(
            value=[],
            placeholder=render_welcome(),
            show_label=False,
            sanitize_html=False,   # 내용은 messages.py 가 escape 해서 만든 HTML 만 들어옴
            buttons=[],
            autoscroll=True,
            elem_id="chat-messages",
        )

        with gr.Row(elem_id="chat-examples") as examples:
            gr.HTML("<span class='ca-label'>예시 질문</span>")
            example_buttons = [gr.Button(q, elem_classes=["ca-chip"]) for q in EXAMPLE_QUESTIONS]

        # 사진을 첨부하면 입력 바 위에 썸네일이 뜨고, 전송할 때 질문과 함께 보냄 (제거 버튼으로 취소)
        with gr.Row(visible=False, elem_id="chat-preview") as preview_row:
            preview = gr.Image(
                show_label=False, interactive=False, buttons=[], container=False, height=96, width=128,
                elem_id="chat-preview-image",
            )
            preview_remove = gr.Button("사진 제거", elem_classes=["ca-button"])

        with gr.Row(elem_id="chat-inputbar", equal_height=True):
            image_button = gr.UploadButton(
                "사진 첨부", file_types=["image"], file_count="single", elem_classes=["ca-button"], elem_id="chat-image"
            )
            message = gr.Textbox(
                show_label=False, placeholder=INPUT_PLACEHOLDER, max_lines=1, container=False, scale=1, elem_id="chat-input"
            )
            send_button = gr.Button("전송", variant="primary", elem_id="chat-send")
    return ChatArea(
        root, equipment, admin_button, theme_button, chatbot, examples, example_buttons,
        preview_row, preview, preview_remove, image_button, message, send_button,
    )
