"""관리자 운영 현황 탭 (SCR-05 · Figma 06 운영 현황, WBS 3.8.3). 막대 · 표는 HTML 로 그리고 모양은 admin.css 의 .adm-*

화면 순서: 기간 표시 → 1열 [일별 질문 수 · 입력 유형 비율 · 설비 모델별 질문 수 (세로로)] | 2열 [자주 묻는 증상 Top 10].
값은 AdminDashboard.usage_stats 결과를 render_usage_tab 에 넣어 만든다. 질문 · 모델명 · 증상 글자는 모두 escape 한다.
"""

from dataclasses import dataclass
from html import escape

import gradio as gr

# 입력 유형 비율 막대 · 범례 순서 (dashboard.INPUT_TYPE_LABELS 의 값과 같은 순서). 색은 admin.css 의 .adm-seg-0 ~ 3
INPUT_TYPE_ORDER = ["텍스트 질문", "명판 사진", "손상 사진", "그 외 사진"]
OTHER_PHOTO = "그 외 사진"   # Figma 에는 텍스트 · 명판 · 손상 3가지만 있어서, 그 외 사진은 건수가 있을 때만 보임
NO_QUESTIONS = "이 기간에는 질문이 없습니다"
DAILY_BAR_MAX_REM = 9.375    # 일별 질문 수 막대 최대 높이 (Figma 150px)
MODEL_BAR_RESERVE_REM = 12.5   # 설비 모델별 한 줄에서 막대 말고 차지하는 너비 (모델명 7.5rem · 간격 · 건수). 가장 긴 막대 = 남는 너비
DENSE_DAYS = 14              # 이보다 날이 많으면(최근 30일) 막대 위 숫자를 빼고 날짜는 띄엄띄엄 표시


@dataclass
class UsageTab:
    """운영 현황 탭에서 새로고침 때 값이 바뀌는 컴포넌트 (기간 선택 포함)."""

    period: gr.Dropdown
    caption: gr.HTML
    daily: gr.HTML
    input_types: gr.HTML
    models: gr.HTML
    top_symptoms: gr.HTML

    def outputs(self) -> list:
        """
        render_usage_tab 이 돌려주는 값과 같은 순서의 컴포넌트 목록 (AdminDashboard.refresh 의 outputs)

        Returns: list
        """
        return [self.caption, self.daily, self.input_types, self.models, self.top_symptoms]


def build_usage_tab(periods: list[str], default: str) -> UsageTab:
    """
    운영 현황 화면 구성 (Figma 06): 기간 표시(기간 선택) · 일별 질문 수 · 입력 유형 비율 · 설비 모델별 질문 수 · 자주 묻는 증상 Top 10.
    기간 선택은 운영 현황 · 답변 품질 탭이 함께 씀 (AdminDashboard.refresh)

    Returns: UsageTab
    """
    with gr.Tab("운영 현황"):
        # 기간 표시 (Figma: 내용 맨 위 "최근 7일 · …"). 명세대로 기간을 고를 수 있게 기간 이름만 드롭다운으로 둠
        with gr.Row(elem_id="admin-period-row"):
            period = gr.Dropdown(   # filterable=False: 글자를 칠 수 없고 목록에서만 고름
                choices=periods, value=default, show_label=False, container=False, filterable=False,
                scale=0, min_width=0, elem_id="admin-period",
            )
            caption = gr.HTML(elem_classes=["adm-caption"])
        with gr.Row(elem_classes=["adm-row"]):
            with gr.Column(elem_classes=["adm-col"]):   # 1열: 일별 → 입력 유형 → 설비 모델 (위에서 아래로)
                daily = gr.HTML()
                input_types = gr.HTML()
                models = gr.HTML()
            with gr.Column(elem_classes=["adm-col"]):   # 2열: 자주 묻는 증상 Top 10
                top_symptoms = gr.HTML()
    return UsageTab(period, caption, daily, input_types, models, top_symptoms)


def render_usage_tab(stats: dict, start, end) -> list:
    """
    usage_stats 결과를 UsageTab.outputs() 순서의 화면 값(HTML)으로 변환. start · end 는 기간 표시에 씀

    Returns: list
    """
    total = stats["total"]
    caption = f"{start:%m/%d}~{end:%m/%d} · " + (f"질문 {total:,}건" if total else NO_QUESTIONS)
    return [
        f"<span class='adm-caption-text'>{caption}</span>",
        render_daily(stats["daily"]),
        render_input_types(stats["input_types"], total),
        render_models(stats["models"]),
        render_top_symptoms(stats["top_symptoms"]),
    ]


def render_daily(daily) -> str:
    """
    일별 질문 수 카드 (Figma: 세로 막대 위에 건수, 아래에 날짜). 날이 많으면 건수는 마우스를 올렸을 때만(title) 보임

    Returns: str (HTML)
    """
    labels, counts = list(daily["날짜"]), [int(c) for c in daily["질문 수"]]
    top = max(counts, default=0)
    dense = len(counts) > DENSE_DAYS
    step = -(-len(labels) // 10) if dense else 1   # 날짜 글자는 10개 안팎만
    bars = "".join(
        f"<div class='adm-bar' title='{escape(label)} · {count}건'>"
        f"<span class='adm-bar-value'>{'' if dense else count}</span>"
        f"<span class='adm-bar-fill' style='height:{count / top * DAILY_BAR_MAX_REM if top else 0:.3f}rem'></span>"
        f"<span class='adm-bar-label'>{escape(label) if i % step == 0 else ''}</span>"
        "</div>"
        for i, (label, count) in enumerate(zip(labels, counts))
    )
    return _card("일별 질문 수", f"<div class='adm-bars{' is-dense' if dense else ''}'>{bars}</div>")


def render_input_types(input_types, total: int) -> str:
    """
    입력 유형 비율 카드 (Figma: 한 줄로 이어 붙인 막대 + 아래 범례 "텍스트 질문  72%")

    Returns: str (HTML)
    """
    if not total:
        return _card("입력 유형 비율", f"<p class='adm-empty'>{NO_QUESTIONS}</p>")
    rows = [
        (name, int(count), float(rate))
        for name, count, rate in zip(input_types["입력 유형"], input_types["건수"], input_types["비율(%)"])
        if count or name != OTHER_PHOTO
    ]
    segments = "".join(
        f"<span class='adm-seg adm-seg-{INPUT_TYPE_ORDER.index(name)}' style='flex-grow:{count}' "
        f"title='{name} {count:,}건'></span>"
        for name, count, _ in rows if count
    )
    legend = "".join(
        f"<li><i class='adm-swatch adm-seg-{INPUT_TYPE_ORDER.index(name)}'></i>"
        f"<span>{name}</span><span class='adm-legend-rate'>{rate:.0f}%</span></li>"
        for name, _, rate in rows
    )
    return _card("입력 유형 비율", f"<div class='adm-stack'>{segments}</div><ul class='adm-legend'>{legend}</ul>")


def render_models(models) -> str:
    """
    설비 모델별 질문 수 카드 (Figma: 모델명 · 가로 막대 · "214건"). 설비 없이 찾은 질문은 "미지정(전체)"

    Returns: str (HTML)
    """
    if models.empty:
        return _card("설비 모델별 질문 수", f"<p class='adm-empty'>{NO_QUESTIONS}</p>")
    counts = [int(c) for c in models["질문 수"]]
    top = max(counts)
    rows = "".join(
        "<li class='adm-hbar'>"
        f"<span class='adm-hbar-label' title='{escape(str(name))}'>{escape(str(name))}</span>"
        f"<span class='adm-hbar-fill' style='width:calc((100% - {MODEL_BAR_RESERVE_REM}rem) * {count / top:.4f})'></span>"
        f"<span class='adm-hbar-value'>{count:,}건</span>"
        "</li>"
        for name, count in zip(models["설비 모델"], counts)
    )
    return _card("설비 모델별 질문 수", f"<ul class='adm-hbars'>{rows}</ul>")


def render_top_symptoms(top_symptoms) -> str:
    """
    자주 묻는 증상 Top 10 카드 (Figma: 순위 · 증상 · 질문 수 표). 한국어 증상이 있으면 그 아래 줄에 영어 원문을 작게 보임

    Returns: str (HTML)
    """
    if top_symptoms.empty:
        return _card("자주 묻는 증상 Top 10", f"<p class='adm-empty'>{NO_QUESTIONS}</p>")
    originals = top_symptoms["원문"] if "원문" in top_symptoms else [""] * len(top_symptoms)
    rows = "".join(
        f"<tr><td class='adm-rank'>{int(rank)}</td><td><span class='adm-symptom'>{escape(str(symptom))}</span>"
        + (f"<span class='adm-symptom-original'>{escape(str(original))}</span>" if original else "")
        + f"</td><td>{int(count):,}</td></tr>"
        for rank, symptom, original, count in zip(
            top_symptoms["순위"], top_symptoms["증상"], originals, top_symptoms["질문 수"]
        )
    )
    table = (
        "<table class='adm-table'><colgroup><col class='adm-col-rank'><col><col class='adm-col-count'></colgroup>"
        f"<thead><tr><th class='adm-rank'>순위</th><th>증상</th><th>질문 수</th></tr></thead><tbody>{rows}</tbody></table>"
    )
    return _card("자주 묻는 증상 Top 10", table)


def _card(title: str, body: str) -> str:
    """흰 카드 (Figma: 테두리 · 둥근 모서리 12px, 제목 굵게 14px). 제목은 코드에서 정한 글자라 escape 하지 않음"""
    return f"<section class='adm-card'><h3 class='adm-card-title'>{title}</h3>{body}</section>"
