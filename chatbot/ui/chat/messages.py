"""채팅창 안에 들어가는 HTML 조각 (Figma 01~04 채팅 화면, 스타일: messages.css)

화면에 넣는 글자는 모두 escape 한다. gr.Chatbot 은 sanitize_html=False 로 쓰기 때문에, 질문·답변 원문에 들어 있는 태그가
그대로 실행되지 않게 하는 곳이 이 파일이다. 한 덩어리 HTML 안에 빈 줄을 넣지 않는다 (마크다운이 중간에서 끊음).
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from chatbot.core.schemas import ActionStep, AnswerResponse, Cause, Figure, Source, Status

MESSAGES_CSS_PATH = Path(__file__).parent / "messages.css"

# 답변이 못 찾음·사진 재촬영 등일 때 카드 제목 (answered 를 뺀 status 전부)
NOTICE_TITLES: dict[Status, str] = {
    "not_found": "문서에서 확인되지 않았습니다",
    "nameplate": "명판을 확인했습니다",
    "retake": "사진만으로 판단하기 어렵습니다",
    "error": "답변을 만들지 못했습니다",
}

NO_EQUIPMENT_TEXT = "전체 매뉴얼"   # 설비를 정하지 않았을 때 (대화 초기화한 상태, Figma 다크 시안 01)


def _circled(number: int) -> str:
    """1 → ①. 20 을 넘으면 '21.' 처럼 씀"""
    return chr(0x2460 + number - 1) if 1 <= number <= 20 else f"{number}."


def render_welcome() -> str:
    """대화를 시작하기 전 빈 채팅창에 보이는 안내 (Figma 01)"""
    return (
        "<div class='wk-welcome'>"
        "<h2 class='wk-welcome-title'>어떤 문제가 있나요?</h2>"
        "<p class='wk-welcome-text'>증상이나 알람 코드를 입력하거나, 명판·손상 부위 사진을 첨부하세요.</p>"
        "<p class='wk-welcome-text'>아래 예시 질문을 누르면 입력창에 채워집니다.</p>"
        "</div>"
    )


def render_user_message(text: str) -> str:
    """사용자 말풍선 (질문 원문은 escape 해서 넣음)"""
    return f"<div class='wk-user-text'>{escape(text or '')}</div>"


def render_equipment(equipment: dict | None) -> str:
    """상단 바의 현재 설비 표시 (C-01, Figma: '설비' + 칩). equipment 는 state["equipment"] ({"model_name", ...}) 이거나 None.
    설비가 있으면 칩을 채워서 모델명을, 없으면 "전체 매뉴얼"을 보여 줌"""
    name = (equipment or {}).get("model_name") if equipment else None
    chip_class = "wk-chip wk-chip-on" if name else "wk-chip"
    return (
        "<div class='wk-equipment'>"
        "<span class='wk-equipment-label'>설비</span>"
        f"<span class='{chip_class}'>{escape(name or NO_EQUIPMENT_TEXT)}</span>"
        "</div>"
    )


def _section(label: str, body: str) -> str:
    return f"<div class='wk-section'><div class='wk-label'>{escape(label)}</div>{body}</div>"


def _render_causes(causes: list[Cause]) -> str:
    """원인마다 '원인: …' 한 줄과 '조치: ① … ② …' 한 줄"""
    parts = []
    for cause in causes:
        actions = " ".join(f"{_circled(i)} {escape(a)}" for i, a in enumerate(cause.actions, start=1))
        parts.append(f"<p class='wk-line'><b>원인</b> {escape(cause.cause)}</p>")
        if actions:
            parts.append(f"<p class='wk-line'><b>조치</b> {actions}</p>")
    return "".join(parts)


def _render_steps(steps: list[ActionStep]) -> str:
    """점검·절차 답변(actions 만 있는 경우)의 단계 목록"""
    return "".join(f"<p class='wk-line'>{_circled(i)} {escape(s.text)}</p>" for i, s in enumerate(steps, start=1))


def _render_sources(sources: list[Source]) -> str:
    """출처 칩 + 원문 보기 (눌러서 펼치는 <details>, 자바스크립트 없이 동작)"""
    return "".join(
        "<details class='wk-source'>"
        f"<summary><span class='wk-chip'>{escape(f'{s.doc_title} · p.{s.page}')}</span><span class='wk-link'>원문 보기</span></summary>"
        f"<blockquote class='wk-quote'>{escape(s.quote)}</blockquote>"
        "</details>"
        for s in sources
    )


def _render_figures(figures: list[Figure]) -> str:
    """그림은 자리 표시만 (실제 이미지는 3.7.4 에서 image_path 로 불러옴)"""
    return "".join(
        "<div class='wk-figure'>"
        "<div class='wk-figure-box'>그림</div>"
        f"<div class='wk-caption'>{escape(f.label)} · {escape(f.caption)}</div>"
        "</div>"
        for f in figures
    )


def render_answer_card(answer: AnswerResponse) -> str:
    """응답 한 건(schemas.AnswerResponse)을 답변 카드 HTML 로 바꿈. 응답 JSON(dict)은 호출하는 쪽이 먼저 검사해서 넘긴다.

    answered 는 요약 → 안전 문구 → 원인·조치 → 그림 → 출처·원문 순서 (Figma 02).
    그 밖의 status(못 찾음·재촬영 등)는 제목과 message 만 있는 안내 카드 (Figma 03·04).
    """
    if answer.status != "answered":
        return (
            "<div class='wk-card wk-notice'>"
            f"<div class='wk-notice-title'>{escape(NOTICE_TITLES[answer.status])}</div>"
            f"<p class='wk-notice-text'>{escape(answer.message or '')}</p>"
            "</div>"
        )

    sections = []
    if answer.summary:
        sections.append(_section("요약", f"<p class='wk-line'>{escape(answer.summary)}</p>"))
    if answer.safety:
        notes = "".join(f"<p class='wk-line'>{escape(n.text)}</p>" for n in answer.safety)
        sections.append(f"<div class='wk-safety'><b class='wk-safety-label'>안전</b><div>{notes}</div></div>")
    if answer.causes:
        sections.append(_section("원인·조치", _render_causes(answer.causes)))
    elif answer.actions:
        sections.append(_section("점검·절차", _render_steps(answer.actions)))
    if answer.figures:
        sections.append(_section("그림", _render_figures(answer.figures)))
    if answer.sources:
        sections.append(_section("출처·원문", _render_sources(answer.sources)))
    return f"<div class='wk-card'>{''.join(sections)}</div>"
