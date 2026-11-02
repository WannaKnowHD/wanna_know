"""관리자 시스템 현황 탭."""

import math

import gradio as gr
import pandas as pd

# 단계별 응답 시간 그래프에 표시할 단계 (표시 순서 고정)
STAGE_NAMES = ["질문 이해", "하이브리드 검색", "근거 검증", "답변 생성"]
# 최근 오류 표의 열 이름
ERROR_HEADERS = ["시각", "단계", "오류 내용"]

STAGE_KEYS = {
    "질문 이해": "understand",
    "하이브리드 검색": "search",
    "근거 검증": "verify",
    "답변 생성": "generate",
}


def aggregate_system_stats(logs: pd.DataFrame) -> dict:
    """DB에서 읽은 기간별 로그로 시스템 지표를 계산한다."""
    timings = [value if isinstance(value, dict) else {} for value in logs["latency_ms"]]

    def average_seconds(key: str) -> float | None:
        values = []

        for timing in timings:
            value = timing.get(key)

            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            ):
                values.append(value)

        return sum(values) / len(values) / 1000 if values else None

    error_text = logs["error"].fillna("").astype(str)
    error_rows = logs.loc[error_text.str.strip().ne("")].copy()
    error_rows = error_rows.sort_values(
        ["created_at", "log_id"],
        ascending=False,
    ).head(20)

    recent_errors = []

    for _, row in error_rows.iterrows():
        text = str(row["error"]).strip()
        stage, separator, detail = text.partition(":")
        recent_errors.append(
            [
                pd.Timestamp(row["created_at"]).strftime("%m/%d %H:%M"),
                stage.strip() if separator else "-",
                detail.strip() if separator else text,
            ]
        )

    return {
        "total_count": len(logs),
        "error_count": int(error_text.str.strip().ne("").sum()),
        "average_seconds": average_seconds("total"),
        "stage_seconds": {
            label: average_seconds(key) for label, key in STAGE_KEYS.items()
        },
        "recent_errors": pd.DataFrame(recent_errors, columns=ERROR_HEADERS),
    }


def format_seconds(seconds: float | None) -> str:
    """
    응답 시간(초)을 화면 표시용 문자열로 변환 (값이 없으면 "-")

    Returns: str
    """
    return "-" if seconds is None else f"{seconds:.1f}초"


def render_metric_card(title: str, value: str, caption: str) -> str:
    """
    상단 지표 카드(평균 전체 응답 시간·오류 비율) HTML

    Returns: str
    """
    return (
        "<div class='system-metric-card'>"
        f"<p class='system-metric-title'>{title}</p>"
        f"<p class='system-metric-value'>{value}</p>"
        f"<p class='system-metric-caption'>{caption}</p>"
        "</div>"
    )


def render_response_time(average_seconds: float | None) -> str:
    """
    평균 전체 응답 시간 카드 HTML

    Returns: str
    """
    return render_metric_card(
        "평균 전체 응답 시간",
        format_seconds(average_seconds),
        "질문 입력 ~ 답변 완료",
    )


def render_error_rate(error_count: int, total_count: int) -> str:
    """
    오류 비율 카드 HTML (전체 요청이 없으면 "-")

    Returns: str
    """
    if total_count == 0:
        return render_metric_card("오류 비율", "-", "요청 없음")
    rate = error_count / total_count * 100
    return render_metric_card(
        "오류 비율",
        f"{rate:.1f}%",
        f"{total_count:,}건 중 {error_count:,}건",
    )


def render_period(period_text: str) -> str:
    """
    집계 기간 표시 HTML (Figma 08: 지표 카드 오른쪽 아래 "최근 7일 (2026-10-21)")

    Returns: str
    """
    return f"<p class='system-period-text'>{period_text}</p>"


def render_stage_times(stage_seconds: dict[str, float | None]) -> str:
    """
    단계별 평균 응답 시간 막대 그래프 HTML. 막대 길이는 가장 오래 걸린 단계 기준 (--ratio = 시간 / 가장 긴 시간),
    시간 글자는 막대 바로 뒤에 붙음 (Figma 08)

    Returns: str
    """
    longest = (
        max(
            (value for value in stage_seconds.values() if value is not None),
            default=0,
        )
        or 1
    )
    rows = []
    for stage in STAGE_NAMES:
        seconds = stage_seconds.get(stage)
        rows.append(
            "<div class='system-stage-row'>"
            f"<span class='system-stage-name'>{stage}</span>"
            f"<span class='system-stage-bar' style='--ratio: {(seconds or 0) / longest:.3f}'></span>"
            f"<span class='system-stage-value'>{format_seconds(seconds)}</span>"
            "</div>"
        )
    return f"<div class='system-stage-chart'>{''.join(rows)}</div>"


def render_system_tab(stats: dict, period_text: str) -> tuple:
    """시스템 통계를 화면 컴포넌트 순서에 맞춰 반환한다."""
    return (
        render_response_time(stats["average_seconds"]),
        render_error_rate(
            stats["error_count"],
            stats["total_count"],
        ),
        render_period(period_text),
        render_stage_times(stats["stage_seconds"]),
        stats["recent_errors"],
    )


def build_system_tab():
    """
    시스템 현황 화면 구성 (평균 전체 응답 시간·오류 비율·단계별 응답 시간·최근 오류).
    값은 AdminDashboard.system_stats 결과를 render_ 함수에 넣어 갱신

    Returns: tuple[gr.HTML, gr.HTML, gr.HTML, gr.HTML, gr.Dataframe]
    """

    with gr.Tab("시스템", elem_id="admin-system-tab"):
        # 상단 지표 카드와 집계 기간
        with gr.Row(elem_classes=["system-metric-row"]):
            response_time_display = gr.HTML(render_response_time(None))
            error_rate_display = gr.HTML(render_error_rate(0, 0))
            period_display = gr.HTML(
                render_period("최근 7일"),
                elem_classes=["system-period"],
            )

        # 단계별 응답 시간
        with gr.Column(elem_classes=["system-card"]):
            gr.HTML("<h3 class='system-card-title'>단계별 응답 시간 (평균)</h3>")
            stage_time_display = gr.HTML(render_stage_times({}))

        # 최근 오류
        with gr.Column(elem_classes=["system-card"]):
            gr.HTML("<h3 class='system-card-title'>최근 오류</h3>")
            recent_error_table = gr.Dataframe(
                headers=ERROR_HEADERS,
                interactive=False,
                buttons=[],
                column_widths=["11.4%", "12%", "76.6%"],   # Figma 08: 시각 140 · 단계 160 · 오류 내용 나머지
                elem_classes=["system-error-table"],
            )

    return (
        response_time_display,
        error_rate_display,
        period_display,
        stage_time_display,
        recent_error_table,
    )
