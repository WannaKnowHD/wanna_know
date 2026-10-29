"""관리자 운영 현황 탭 (SCR-05, WBS 3.8.3)."""

from dataclasses import dataclass

import gradio as gr

# 입력 유형 막대 순서 (dashboard.INPUT_TYPE_LABELS의 값과 같은 순서)
INPUT_TYPE_ORDER = ["텍스트 질문", "명판 사진", "손상 사진", "그 외 사진"]


@dataclass
class UsageTab:
    """운영 현황 탭에서 새로고침 때 값이 바뀌는 컴포넌트."""

    summary: gr.Markdown
    daily_plot: gr.BarPlot
    input_plot: gr.BarPlot
    model_plot: gr.BarPlot
    symptom_table: gr.Dataframe

    def outputs(self) -> list:
        """
        render_usage_tab 이 돌려주는 값과 같은 순서의 컴포넌트 목록 (AdminDashboard.refresh 의 outputs)

        Returns: list
        """
        return [
            self.summary,
            self.daily_plot,
            self.input_plot,
            self.model_plot,
            self.symptom_table,
        ]


def build_usage_tab() -> UsageTab:
    """
    운영 현황 화면 구성 (기간 요약·일별 질문 수·입력 유형 비율·설비 모델별 질문 수·자주 묻는 증상 Top 10).
    값은 AdminDashboard.usage_stats 결과를 render_usage_tab 에 넣어 갱신

    Returns: UsageTab
    """

    with gr.Tab("운영 현황"):
        summary = gr.Markdown(elem_classes=["admin-summary"])
        with gr.Row(equal_height=True):
            daily_plot = gr.BarPlot(
                x="날짜",
                y="질문 수",
                title="일별 질문 수",
                sort="x",
                # Gradio는 축을 값 범위에 맞춰 잘라 작은 막대가 사라지므로 0부터 그림
                y_lim=[0, None],
                height=260,
                elem_classes=["admin-panel"],
            )
            input_plot = gr.BarPlot(
                x="입력 유형",
                y="비율(%)",
                title="입력 유형 비율",
                sort=INPUT_TYPE_ORDER,
                tooltip="all",
                y_lim=[0, None],
                height=260,
                elem_classes=["admin-panel"],
            )
        with gr.Row(equal_height=True):
            model_plot = gr.BarPlot(
                x="설비 모델",
                y="질문 수",
                title="설비 모델별 질문 수",
                sort="-y",
                y_lim=[0, None],
                height=260,
                elem_classes=["admin-panel"],
            )
            symptom_table = gr.Dataframe(
                headers=["순위", "증상", "질문 수"],
                datatype=["number", "str", "number"],
                label="자주 묻는 증상 Top 10",
                interactive=False,
                wrap=True,
                elem_classes=["admin-panel"],
            )
    return UsageTab(summary, daily_plot, input_plot, model_plot, symptom_table)


def render_usage_tab(stats: dict, period_label: str) -> list:
    """
    usage_stats 결과를 UsageTab.outputs() 순서의 화면 값으로 변환 (질문이 없는 기간이면 요약에 안내 문구)

    Returns: list
    """
    if stats["total"]:
        summary = f"**{period_label}** · 질문 {stats['total']:,}건"
    else:
        summary = f"**{period_label}** · 이 기간에는 질문이 없습니다"
    return [
        summary,
        stats["daily"],
        stats["input_types"],
        stats["models"],
        stats["top_symptoms"],
    ]
