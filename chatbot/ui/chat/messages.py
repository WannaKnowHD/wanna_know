"""채팅창 안에 들어가는 HTML 조각 (Figma 01~04 채팅 화면, 스타일: messages.css)

화면에 넣는 글자는 모두 escape 한다. gr.Chatbot 은 sanitize_html=False 로 쓰기 때문에, 질문·답변 원문에 들어 있는 태그가
그대로 실행되지 않게 하는 곳이 이 파일이다. 한 덩어리 HTML 안에 빈 줄을 넣지 않는다 (마크다운이 중간에서 끊음).
"""
from __future__ import annotations

import base64
from functools import lru_cache
from html import escape
from pathlib import Path

from chatbot.core.schemas import ActionStep, AnswerResponse, Cause, Figure, SafetyNote, Source, Status

MESSAGES_CSS_PATH = Path(__file__).parent / "messages.css"

# 답변이 못 찾음·사진 재촬영 등일 때 카드 제목 (answered 를 뺀 status 전부)
NOTICE_TITLES: dict[Status, str] = {
    "not_found": "문서에서 확인되지 않았습니다",
    "nameplate": "명판을 확인했습니다",
    "retake": "사진만으로 판단하기 어렵습니다",
    "error": "답변을 만들지 못했습니다",
}

# 사진 재촬영 안내 카드의 버튼 (Figma 04 '다시 촬영'): 입력 바의 사진 첨부(#chat-image)를 눌러 파일 선택 창을 엶
RETAKE_BUTTON_HTML = (
    "<button type='button' class='wk-retake' onclick=\"document.querySelector('#chat-image')?.click()\">다시 촬영</button>"
)

TYPING_TEXT = "매뉴얼에서 근거를 확인하고 있어요…"   # 답변 생성 중 (Figma 01-1)
TYPING_NOTE = "답변이 준비되면 바로 표시됩니다"
NO_IMAGE_TEXT = "이미지를 불러올 수 없습니다"   # image_path 의 파일이 없거나 읽을 수 없을 때 (카드는 깨지지 않고 글자만 보임)
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}   # 이 확장자만 화면에 넣음
TABLE_FIGURE_TYPES = ("table", "figure")   # 원문 보기에 잘라 둔 이미지를 같이 보여 주는 근거 종류


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


def render_typing() -> str:
    """답변을 만드는 동안 채팅창에 보이는 말풍선 (Figma 01-1 답변 생성 중: 점 3개 + 안내 + 아래 작은 안내).
    Gradio 처리 중 표시(주황 로딩 · processing 시간)는 숨겼으므로 이 말풍선이 기다리는 동안의 표시. 답변 카드가 오면 이 자리가 바뀜"""
    return (
        "<div class='wk-typing'>"
        f"<div class='wk-typing-bubble'><span class='wk-dots'><i></i><i></i><i></i></span><span>{TYPING_TEXT}</span></div>"
        f"<p class='wk-typing-note'>{TYPING_NOTE}</p>"
        "</div>"
    )


@lru_cache(maxsize=64)
def _data_uri(path: str) -> str | None:
    """잘라 둔 이미지(crop_regions 가 만든 파일)를 data URI 로 바꿈. 없거나 읽을 수 없으면 None.
    채팅창은 로컬 파일 주소를 바로 열 수 없어서 HTML 안에 이미지를 넣는다. 한 번 읽은 파일은 기억해 둔다."""
    file = Path(path)
    mime = IMAGE_TYPES.get(file.suffix.lower())
    if mime is None:
        return None
    try:
        return f"data:{mime};base64,{base64.b64encode(file.read_bytes()).decode('ascii')}"
    except OSError:
        return None


def _image(path: str | None, alt: str) -> str:
    """이미지 <img> 한 개. 이미지를 못 읽으면 빈 글자 (호출하는 쪽이 대체 글자를 정함)"""
    uri = _data_uri(path) if path else None
    return f"<img class='wk-image' src='{uri}' alt='{escape(alt)}'>" if uri else ""


def _section(label: str, body: str) -> str:
    return f"<div class='wk-section'><div class='wk-label'>{escape(label)}</div>{body}</div>"


def _merge_causes(causes: list[Cause]) -> list[tuple[str, list[str]]]:
    """같은 원인 문장은 하나로 합치고 조치는 순서를 지키며 중복을 뺀다 (KB 여러 개가 같은 원인을 돌려줄 때 반복돼 보이지 않게)"""
    merged: dict[str, list[str]] = {}
    for cause in causes:
        actions = merged.setdefault(cause.cause.strip(), [])
        actions.extend(a for a in cause.actions if a not in actions)
    return list(merged.items())


def _render_causes(causes: list[Cause]) -> str:
    """원인마다 번호 붙은 제목 한 줄과, 그 아래 조치 목록 (순서대로 점검)"""
    blocks = []
    for number, (cause, actions) in enumerate(_merge_causes(causes), start=1):
        steps = "".join(f"<li>{escape(a)}</li>" for a in actions)
        action_list = f"<ol class='wk-actions'>{steps}</ol>" if steps else ""
        blocks.append(
            "<div class='wk-cause'>"
            f"<div class='wk-cause-head'><span class='wk-cause-no'>{number}</span>{escape(cause)}</div>"
            f"{action_list}</div>"
        )
    return "".join(blocks)


def _render_safety_note(note: SafetyNote) -> str:
    """안전 문구 한 줄. 매뉴얼 출처(문서 · 페이지)가 있으면 뒤에 붙임 (E-02)"""
    origin = ""
    if note.doc_title and note.page is not None:
        origin = f" <span class='wk-safety-source'>({escape(note.doc_title)} · p.{note.page})</span>"
    return f"<p class='wk-line'>{escape(note.text)}{origin}</p>"


def _render_steps(steps: list[ActionStep]) -> str:
    """점검·절차 답변(actions 만 있는 경우)의 단계 목록"""
    return "".join(f"<p class='wk-line'>{_circled(i)} {escape(s.text)}</p>" for i, s in enumerate(steps, start=1))


def _render_sources(sources: list[Source]) -> str:
    """출처 칩 + 원문 보기 (눌러서 펼치는 <details>, 자바스크립트 없이 동작). 근거가 표·그림이면 잘라 둔 이미지도 같이 보여 줌"""
    parts = []
    for s in sources:
        picture = _image(s.image_path, f"{s.doc_title} p.{s.page}") if s.chunk_type in TABLE_FIGURE_TYPES else ""
        parts.append(
            "<details class='wk-source'>"
            f"<summary><span class='wk-chip'>{escape(f'{s.doc_title} · p.{s.page}')}</span><span class='wk-link'>원문 보기</span></summary>"
            f"{picture}<blockquote class='wk-quote'>{escape(s.quote)}</blockquote>"
            "</details>"
        )
    return "".join(parts)


def _render_figures(figures: list[Figure]) -> str:
    """그림: image_path 의 이미지와 '그림 번호 · 설명'. 이미지를 못 읽으면 자리 표시 글자만 보임"""
    parts = []
    for f in figures:
        picture = _image(f.image_path, f.caption) or f"<div class='wk-figure-box'>{NO_IMAGE_TEXT}</div>"
        parts.append(f"<div class='wk-figure'>{picture}<div class='wk-caption'>{escape(f.label)} · {escape(f.caption)}</div></div>")
    return "".join(parts)


def _notice_title(answer: AnswerResponse) -> str:
    """안내 카드 제목. 명판으로 설비를 정하면 모델명, 사진 재촬영이면 확신도를 붙임 (Figma 04)"""
    if answer.status == "nameplate" and answer.equipment is not None:
        return f"명판에서 모델명 {answer.equipment.model}을(를) 확인했습니다"
    title = NOTICE_TITLES[answer.status]
    if answer.status == "retake" and answer.image is not None:
        confidence = answer.image.damage.confidence if answer.image.damage else answer.image.confidence
        return f"{title} (확신도 {confidence:.2f})"
    return title


def render_answer_card(answer: AnswerResponse) -> str:
    """응답 한 건(schemas.AnswerResponse)을 답변 카드 HTML 로 바꿈. 응답 JSON(dict)은 호출하는 쪽이 먼저 검사해서 넘긴다.

    answered 는 요약 → 안전 문구 → 원인·조치 → 그림 → 출처·원문 순서 (Figma 02).
    그 밖의 status(못 찾음·재촬영 등)는 제목과 message 만 있는 안내 카드 (Figma 03·04).
    """
    if answer.status != "answered":
        button = RETAKE_BUTTON_HTML if answer.status == "retake" else ""
        return (
            "<div class='wk-card wk-notice'>"
            f"<div class='wk-notice-title'>{escape(_notice_title(answer))}</div>"
            f"<p class='wk-notice-text'>{escape(answer.message or '')}</p>"
            f"{button}"
            "</div>"
        )

    sections = []
    if answer.summary:
        sections.append(_section("요약", f"<p class='wk-line'>{escape(answer.summary)}</p>"))
    if answer.safety:
        notes = "".join(_render_safety_note(n) for n in answer.safety)
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
