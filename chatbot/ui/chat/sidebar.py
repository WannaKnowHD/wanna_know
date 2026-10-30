"""채팅 화면 왼쪽 사이드바 (Figma 01 대화 기록창: 제목·대화 초기화·대화 기록·사용자 정보·로그아웃, 스타일: sidebar.css)"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr

if TYPE_CHECKING:
    from chatbot.core.schemas import User

SIDEBAR_CSS_PATH = Path(__file__).parent / "sidebar.css"

BRAND = "컨베이어 문제 해결 챗봇"   # 사이드바 맨 위 제목 (Figma Page 3 대화 기록창). 서비스 이름 Wanna Know 는 채팅 상단 바

# 대화 기록 항목을 누르면(Enter · Space 도) 그 항목의 순서와 history_key 를 담아 click 이벤트를 보냄 → ChatApp.open_history (C-03).
# 목록 HTML 은 값이 바뀔 때마다 다시 그려지므로 항목마다가 아니라 목록 전체(element)에 한 번 걸어 둔다
HISTORY_CLICK_JS = """
const pick = (item) => {
  element.querySelectorAll('.history-item.is-active').forEach((el) => el.classList.remove('is-active'));
  item.classList.add('is-active');
  trigger('click', {index: Number(item.dataset.index), key: item.dataset.key});
};
element.addEventListener('click', (event) => {
  const item = event.target.closest('.history-item');
  if (item) pick(item);
});
element.addEventListener('keydown', (event) => {
  const item = event.target.closest('.history-item');
  if (item && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    pick(item);
  }
});
"""


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
        history = gr.HTML("", elem_id="sb-history", js_on_load=HISTORY_CLICK_JS)   # 항목을 누르면 history.click
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


def render_history(rows: list[dict], now: datetime | None = None) -> str:
    """과거 대화 기록(load_history 결과, 최신순)을 사이드바 목록 HTML 로 바꿈 (Figma Sidebar/Item: 질문 + "오늘 10:47").
    항목마다 순서(data-index)와 history_key(data-key)를 달아 두고, 누르면 ChatApp.open_history 가 그 대화를 채팅창에 보여 줌.
    질문 원문은 escape 해서 넣음"""
    if not rows:
        return "<p class='sb-empty'>아직 대화 기록이 없습니다.</p>"
    now = now or datetime.now()
    items = "".join(
        f"<li class='history-item' role='button' tabindex='0' data-index='{i}' data-key='{escape(history_key(r))}'>"
        f"<span class='history-question'>{escape(str(r.get('question', '')))}</span>"
        f"<span class='history-time'>{escape(format_history_time(r.get('created_at'), now))}</span>"
        "</li>"
        for i, r in enumerate(rows)
    )
    return f"<ul class='history-list'>{items}</ul>"


def history_key(row: dict) -> str:
    """대화 기록 한 건을 가리키는 값 (저장 시각 + 질문). 목록을 그린 뒤 새 대화가 저장돼 순서가 밀려도 이 값으로 다시 찾음.
    저장 시각은 초 단위(MySQL DATETIME)라 시각만으로는 겹칠 수 있어 질문도 붙임"""
    return f"{row.get('created_at', '')}|{row.get('question', '')}"


def format_history_time(value, now: datetime) -> str:
    """대화 기록의 시각 표시 (Figma: "오늘 10:47", "어제 16:20", 그 전은 "10/27 09:13")"""
    if isinstance(value, datetime):
        when = value
    elif isinstance(value, date):
        when = datetime.combine(value, datetime.min.time())
    else:
        try:
            when = datetime.fromisoformat(str(value))
        except ValueError:   # 형식을 모르면 앞 16자(YYYY-MM-DD HH:MM)만
            return str(value or "")[:16]
    if when.date() == now.date():
        return f"오늘 {when:%H:%M}"
    if when.date() == now.date() - timedelta(days=1):
        return f"어제 {when:%H:%M}"
    return f"{when:%m/%d %H:%M}"
