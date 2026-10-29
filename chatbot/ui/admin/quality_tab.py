"""관리자 답변 품질 탭 (SCR-06, WBS 3.8.4)."""

from dataclasses import dataclass

import gradio as gr
import pandas as pd

# 답 못 찾은 질문 목록의 열. "No"는 chat_logs.log_id라 체크박스를 저장할 때 씀
REVIEWED = "담당자 확인"
KB_UPDATE_NEEDED = "KB 업데이트 필요"
NOT_FOUND_HEADERS = [
    "No",
    "시각",
    "질문",
    "설비",
    "검색 점수",
    REVIEWED,
    KB_UPDATE_NEEDED,
]
NOT_FOUND_TYPES = ["number", "str", "str", "str", "number", "bool", "bool"]


@dataclass
class QualityTab:
    """답변 품질 탭에서 새로고침 때 값이 바뀌는 컴포넌트."""

    not_found_card: gr.HTML
    safety_card: gr.HTML
    not_found_table: gr.Dataframe
    review_status: gr.Markdown

    def outputs(self) -> list:
        """
        render_quality_tab 이 돌려주는 값과 같은 순서의 컴포넌트 목록 (AdminDashboard.refresh 의 outputs)

        Returns: list
        """
        return [
            self.not_found_card,
            self.safety_card,
            self.not_found_table,
            self.review_status,
        ]


def build_quality_tab() -> QualityTab:
    """
    답변 품질 화면 구성 (답을 못 찾은 비율·안전 문구 포함률 카드, 답 못 찾은 질문 목록).
    값은 AdminDashboard.quality_stats · not_found_list 결과를 render_quality_tab 에 넣어 갱신.
    목록의 체크박스를 바꾸면 AdminDashboard.on_review_input 이 kb_gaps 에 저장

    Returns: QualityTab
    """

    with gr.Tab("답변 품질"):
        with gr.Row(equal_height=True):
            not_found_card = gr.HTML()
            safety_card = gr.HTML()
        gr.Markdown(
            "#### 답 못 찾은 질문 목록\n"
            "확인한 질문과 KB 보강이 필요한 질문에 체크하면 바로 저장됩니다.",
            elem_classes=["admin-summary"],
        )
        not_found_table = gr.Dataframe(
            headers=NOT_FOUND_HEADERS,
            datatype=NOT_FOUND_TYPES,
            interactive=True,
            static_columns=[0, 1, 2, 3, 4],  # 체크박스 두 열만 고칠 수 있음
            column_widths=["7%", "13%", "42%", "13%", "9%", "8%", "8%"],
            max_height=480,
            wrap=True,
            elem_id="not-found-table",
            elem_classes=["admin-panel"],
        )
        review_status = gr.Markdown(elem_classes=["admin-note"])
    return QualityTab(not_found_card, safety_card, not_found_table, review_status)


def render_quality_tab(stats: dict, not_found: pd.DataFrame) -> list:
    """
    quality_stats · not_found_list 결과를 QualityTab.outputs() 순서의 화면 값으로 변환 (저장 안내 문구는 비움)

    Returns: list
    """
    return [
        _rate_card(
            "답을 못 찾은 비율",
            stats["not_found_rate"],
            f"{stats['total']:,}건 중 {stats['not_found']:,}건",
        ),
        _rate_card(
            "안전 문구 포함률",
            stats["safety_rate"],
            f"답변 {stats['answered']:,}건 중 {stats['safety_included']:,}건",
        ),
        not_found,
        "",
    ]


def _rate_card(title: str, rate: float, detail: str) -> str:
    """비율 카드 HTML. 글자는 모두 코드에서 만든 값이라 이스케이프하지 않음"""
    return (
        "<div class='admin-card'>"
        f"<div class='admin-card-title'>{title}</div>"
        f"<div class='admin-card-value'>{rate:.1%}</div>"
        f"<div class='admin-card-detail'>{detail}</div>"
        "</div>"
    )
