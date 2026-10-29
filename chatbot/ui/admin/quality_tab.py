"""관리자 답변 품질 탭 (SCR-06 · Figma 07 답변 품질, WBS 3.8.4). 모양은 admin.css 의 .adm-* · #admin-not-found

화면 순서 (Figma 07): 지표 카드(답을 못 찾은 비율) → 답 못 찾은 질문 목록 카드(설명 + 체크박스 표).
목록의 체크박스를 바꾸면 AdminDashboard.on_review_input 이 kb_gaps 에 바로 저장한다.
"""

from dataclasses import dataclass

import gradio as gr
import pandas as pd

REVIEWED = "담당자 확인"
KB_UPDATE_NEEDED = "KB 업데이트 필요"
# 답 못 찾은 질문 목록의 열 (Figma 07: 날짜 · 질문 · 설비 · 담당자 확인 · KB 업데이트 필요).
# 맨 뒤 "No"는 chat_logs.log_id 로, 체크박스를 저장할 때 행을 찾는 데 쓰고 화면에서는 admin.css 가 숨김
# (맨 앞에 두면 Gradio 가 첫 칸을 따로 그려 제목 행과 본문 행의 칸이 어긋남)
NOT_FOUND_HEADERS = ["날짜", "질문", "설비", REVIEWED, KB_UPDATE_NEEDED, "No"]
NOT_FOUND_TYPES = ["str", "str", "str", "bool", "bool", "number"]
REVIEW_HELP = (   # Figma 07 문구 그대로
    "체크박스를 누르면 바로 저장됩니다. 확인한 질문은 '담당자 확인', KB 보강이 필요하면 'KB 업데이트 필요'도 체크하세요."
)


@dataclass
class QualityTab:
    """답변 품질 탭에서 새로고침 때 값이 바뀌는 컴포넌트."""

    kpis: gr.HTML
    not_found_table: gr.Dataframe
    review_status: gr.HTML

    def outputs(self) -> list:
        """
        render_quality_tab 이 돌려주는 값과 같은 순서의 컴포넌트 목록 (AdminDashboard.refresh 의 outputs)

        Returns: list
        """
        return [self.kpis, self.not_found_table, self.review_status]


def build_quality_tab() -> QualityTab:
    """
    답변 품질 화면 구성 (Figma 07): 지표 카드, 답 못 찾은 질문 목록(설명 + 체크박스 표 + 저장 안내).
    값은 AdminDashboard.quality_stats · not_found_list 결과를 render_quality_tab 에 넣어 갱신

    Returns: QualityTab
    """
    with gr.Tab("답변 품질"):
        kpis = gr.HTML()
        with gr.Column(elem_id="admin-not-found"):   # 카드 하나: 제목 · 설명 · 표 · 저장 안내
            gr.HTML(f"<h3 class='adm-card-title'>답 못 찾은 질문 목록</h3><p class='adm-help'>{REVIEW_HELP}</p>")
            not_found_table = gr.Dataframe(
                headers=NOT_FOUND_HEADERS,
                datatype=NOT_FOUND_TYPES,
                interactive=True,
                static_columns=[0, 1, 2, 5],   # 체크박스 두 열만 고칠 수 있음
                column_widths=["9%", "58%", "10%", "10%", "13%", "0%"],   # Figma 열 너비 110 · 790 · 120 · 120 · 170, No 는 0
                buttons=[],
                show_label=False,
                max_height=480,
                wrap=True,
                elem_id="not-found-table",
            )
            review_status = gr.HTML(elem_classes=["adm-review-status"])
    return QualityTab(kpis, not_found_table, review_status)


def render_quality_tab(stats: dict, not_found: pd.DataFrame, period: str) -> list:
    """
    quality_stats · not_found_list 결과를 QualityTab.outputs() 순서의 화면 값으로 변환 (저장 안내 문구는 비움).
    period 는 기간 이름(예: 최근 7일). Figma 지표 카드 설명 "최근 7일 · 812건 중 68건"에 씀

    Returns: list
    """
    cards = _kpi_card(
        "답을 못 찾은 비율", stats["not_found_rate"], f"{period} · {stats['total']:,}건 중 {stats['not_found']:,}건"
    ) + _kpi_card(   # WBS 3.8.4 지표. Figma 지표 카드와 같은 모양으로 옆에 둠
        "안전 문구 포함률", stats["safety_rate"],
        f"{period} · 답변 {stats['answered']:,}건 중 {stats['safety_included']:,}건",
    )
    return [f"<div class='adm-kpis'>{cards}</div>", not_found, ""]


def _kpi_card(label: str, rate: float, note: str) -> str:
    """지표 카드 (Figma KPI Card 260x116: 이름 · 큰 값 · 설명). 글자는 모두 코드에서 만든 값이라 escape 하지 않음"""
    return (
        "<div class='adm-kpi'>"
        f"<span class='adm-kpi-label'>{label}</span>"
        f"<span class='adm-kpi-value'>{rate:.1%}</span>"
        f"<span class='adm-kpi-note'>{note}</span>"
        "</div>"
    )
