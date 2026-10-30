"""로그인 화면 (Figma 00 로그인, 스타일: login_form.css)"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

import gradio as gr

from chatbot.ui.login.theme import THEME_TOGGLE_ENABLED

LOGIN_FORM_CSS_PATH = Path(__file__).parent / "login_form.css"

# 안내 문구 (기능명세서 on_login)
MSG_EMPTY = "사번과 비밀번호를 입력하세요."
MSG_INVALID = "사번 또는 비밀번호가 올바르지 않습니다."   # Figma 00 로그인 실패 안내
MSG_SERVER = "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."

LOGIN_HEADER_HTML = (
    "<div class='login-header'>"
    # 라이트: 초록 태그 + "Wanna Know" / 다크(딥 포레스트): 알약 태그 + 한글 제목 (Figma 00 로그인 두 시안)
    "<span class='login-tag theme-light-only'>컨베이어 문제 해결 챗봇</span>"
    "<span class='login-tag theme-dark-only'>AI TROUBLESHOOTING ASSISTANT</span>"
    "<h1 class='login-title theme-light-only'>Wanna Know</h1>"
    "<h1 class='login-title theme-dark-only'>컨베이어 문제 해결 챗봇</h1>"
    "<p class='login-subtitle'>사번과 비밀번호로 로그인하세요</p>"
    "</div>"
)
LOGIN_NOTE_HTML = "<p class='login-note'>관리자 대시보드는 로그인 후 채팅 화면의 '관리자 전환'에서 들어갑니다.</p>"


@dataclass
class LoginForm:
    """build_login_form 이 만든 화면 요소 (이벤트를 연결할 때 씀)"""

    root: gr.Column
    employee: gr.Textbox
    password: gr.Textbox
    error: gr.HTML
    button: gr.Button
    theme_button: gr.Button


def build_login_form() -> LoginForm:
    """로그인 화면 구성: 사원번호·비밀번호 입력창, 안내문, 로그인 버튼, 라이트/다크 전환 버튼"""
    with gr.Column(elem_id="login-root") as root:
        with gr.Column(elem_id="login-card"):
            gr.HTML(LOGIN_HEADER_HTML)
            employee = gr.Textbox(label="사번", placeholder="예: 20261234", max_lines=1, elem_id="login-employee")
            password = gr.Textbox(
                label="비밀번호", type="password", placeholder="비밀번호", max_lines=1, elem_id="login-password"
            )
            error = gr.HTML("", visible=False, elem_id="login-error")
            button = gr.Button("로그인", variant="primary", elem_id="login-submit")
            gr.HTML(LOGIN_NOTE_HTML)
        # Figma 로그인 카드에는 전환 버튼이 없어서 카드 밖(화면 오른쪽 위)에 둠
        theme_button = gr.Button(
            "라이트 / 다크 전환", elem_id="login-theme", elem_classes=["theme-toggle"], size="sm",
            visible=THEME_TOGGLE_ENABLED,
        )
    return LoginForm(root, employee, password, error, button, theme_button)


def render_login_error(message: str | None):
    """로그인 실패 안내를 보이거나 숨기는 gr.update. 문구는 escape 해서 넣는다."""
    html = f"<p class='login-error-text'>{escape(message)}</p>" if message else ""
    return gr.update(value=html, visible=bool(message))
