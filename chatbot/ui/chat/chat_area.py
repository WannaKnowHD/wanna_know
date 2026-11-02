"""채팅 화면 오른쪽 채팅 영역 (Figma 01~04: 상단 바·메시지·예시 질문·입력 바, 스타일: chat_area.css)"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gradio as gr

from chatbot.ui.chat.messages import render_welcome
from chatbot.ui.login.theme import THEME_TOGGLE_ENABLED

CHAT_AREA_CSS_PATH = Path(__file__).parent / "chat_area.css"
CHAT_ASSET_DIR = Path(__file__).parent / "assets"           # 화면에 쓰는 이미지 (launch 의 allowed_paths 로 내보냄)
LOGO_PATH = CHAT_ASSET_DIR / "symbol_logo.png"               # Figma 상단 바 symbol_logo (40px)

# 예시 질문 템플릿 (Q-06, show_examples 가 돌려줌). 지금 매뉴얼 3종의 KB 로 답할 수 있는 질문만 둔다
# (화면설계서 시안의 "E-102 알람이 떠요"는 KB 에 없는 알람 코드라 "확인되지 않음"이 나와서 뺌)
EXAMPLE_QUESTIONS = (
    "모터가 너무 뜨거워요",        # TS-HYTROL138ACC-T01-04 · TS-PRECOLSERIES-T04-03
    "벨트가 한쪽으로 쏠려요",      # TS-FENNERBELT-T01-02 · TS-PRECOLSERIES-T04-04
    "벨트가 미끄러져요",           # TS-FENNERBELT-T01-07 · TS-HYTROL138ACC-T02-01
    "벨트 가장자리가 닳았어요",    # TS-FENNERBELT-T01-18
    "주간 점검 뭐 해야 해?",       # maintenance KB 중 interval "weekly" 6개
)
# 예시 질문 버튼 글자 → 챗봇에 실제로 넘기는 질문 (ChatApp.on_submit 에서 바꿈)
# 버튼 · 입력창 · 말풍선 · 대화 기록은 짧은 글자 그대로 두고, 무엇에 대한 질문인지만 붙여 검색이 정답 KB 를 찾게 한다.
# 문장은 실제 챗봇으로 확인한 것 ("컨베이어 벨트 가장자리가 닳았어요"는 리랭커 점수가 기준값 미만이라 "손상" 을 넣음)
EXAMPLE_FULL_QUESTIONS = {
    "모터가 너무 뜨거워요": "컨베이어 모터가 너무 뜨거워요",
    "벨트가 한쪽으로 쏠려요": "컨베이어 벨트가 한쪽으로 쏠려요",
    "벨트가 미끄러져요": "컨베이어 벨트가 미끄러져요",
    "벨트 가장자리가 닳았어요": "컨베이어 벨트 가장자리가 닳고 손상됐어요",
    "주간 점검 뭐 해야 해?": "컨베이어 주간 점검은 뭘 해야 하나요?",
}
SERVICE_NAME = "컨베이어 문제 해결 챗봇"   # 채팅 상단 바 왼쪽 (예전 설비 표시 자리)
INPUT_PLACEHOLDER = "증상이나 알람 코드를 입력하세요"

# 새 질문을 보내면 방금 보낸 질문 말풍선(사진 + 글자)이 채팅창 위쪽에 오도록 한 번만 스크롤 (gradio_app 의 질문 표시 → 답변 사이에 실행).
# 채팅창은 autoscroll=False 라 답변이 길어질 때는 따라가지 않음. 답변 생성 중 말풍선(.wk-typing)이 그려질 때까지 최대 1.5초 기다림.
# 사이드바 대화 기록 목록도 맨 위로 올려 지금 대화(목록 맨 위)가 보이게 함
SCROLL_TO_QUESTION_JS = """
() => {
  document.querySelector('#sb-history')?.scrollTo({ top: 0, behavior: 'smooth' });   // 사이드바 대화 기록도 맨 위(지금 대화)로
  const started = Date.now();
  const tick = () => {
    const wrap = document.querySelector('#chat-messages .bubble-wrap');
    const typing = wrap && wrap.querySelector('.wk-typing');
    if (!typing) { if (Date.now() - started < 1500) requestAnimationFrame(tick); return; }
    const before = (el) => el.compareDocumentPosition(typing) & Node.DOCUMENT_POSITION_FOLLOWING;
    const bots = [...wrap.querySelectorAll('.message.bot')].filter(before);
    const lastBot = bots[bots.length - 1];
    const asked = [...wrap.querySelectorAll('.message.user')].filter(
      (el) => before(el) && (!lastBot || lastBot.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)
    );
    const target = asked[0] || typing;
    const offset = target.getBoundingClientRect().top - wrap.getBoundingClientRect().top;
    wrap.scrollTo({ top: wrap.scrollTop + offset - 16, behavior: 'smooth' });
  };
  requestAnimationFrame(tick);
}
"""


@dataclass
class ChatArea:
    """build_chat_area 가 만든 화면 요소 (이벤트를 연결할 때 씀)"""

    root: gr.Column
    brand: gr.HTML
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
    """채팅 영역 구성: 로고·챗봇 이름 칩·관리자 전환·라이트/다크 버튼, 채팅창, 예시 질문, 사진 첨부·질문 입력·전송"""
    with gr.Column(scale=1, elem_id="chat-area") as root:
        with gr.Row(elem_id="chat-topbar", equal_height=True):
            brand = gr.HTML(   # Figma 상단 바 왼쪽: symbol_logo 40px + 칩
                "<div class='ca-brand'>"
                f"<img class='ca-logo' src='/gradio_api/file={LOGO_PATH.as_posix()}' alt=''>"
                f"<span class='ca-service-chip'>{SERVICE_NAME}</span>"
                "</div>",
                elem_id="chat-brand",
            )
            admin_button = gr.Button("관리자 전환", visible=False, elem_classes=["ca-button"])
            theme_button = gr.Button(
                "테마 전환", elem_classes=["ca-button", "theme-toggle"], visible=THEME_TOGGLE_ENABLED
            )

        chatbot = gr.Chatbot(
            value=[],
            placeholder=render_welcome(),
            show_label=False,
            sanitize_html=False,   # 내용은 messages.py 가 escape 해서 만든 HTML 만 들어옴
            buttons=[],
            autoscroll=False,   # 답변이 길어져도 사용자가 읽던 위치를 강제로 끝으로 옮기지 않음
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
        root, brand, admin_button, theme_button, chatbot, examples, example_buttons,
        preview_row, preview, preview_remove, image_button, message, send_button,
    )
