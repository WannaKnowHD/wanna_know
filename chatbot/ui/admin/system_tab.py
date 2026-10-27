"""관리자 시스템 현황 탭."""

import gradio as gr

# 단계별 응답 시간 그래프에 표시할 단계 (표시 순서 고정)
STAGE_NAMES = ["질문 이해", "하이브리드 검색", "근거 검증", "답변 생성"]
# 최근 오류 표의 열 이름
ERROR_HEADERS = ["시각", "단계", "오류 내용"]


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
        "질문 입력 → 답변 완료",
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
    집계 기간 표시 HTML

    Returns: str
    """
    return f"<p class='system-period-text'>집계 기간 · {period_text}</p>"


def render_stage_times(stage_seconds: dict[str, float]) -> str:
    """
    단계별 평균 응답 시간 막대 그래프 HTML. 막대 길이는 가장 오래 걸린 단계 기준

    Returns: str
    """
    longest = max(stage_seconds.values(), default=0) or 1
    rows = []
    for stage in STAGE_NAMES:
        seconds = stage_seconds.get(stage)
        rows.append(
            "<div class='system-stage-row'>"
            f"<span class='system-stage-name'>{stage}</span>"
            f"<progress class='system-stage-bar' value='{seconds or 0}' "
            f"max='{longest}'></progress>"
            f"<span class='system-stage-value'>{format_seconds(seconds)}</span>"
            "</div>"
        )
    return f"<div class='system-stage-chart'>{''.join(rows)}</div>"


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
                elem_classes=["system-error-table"],
            )

    return (
        response_time_display,
        error_rate_display,
        period_display,
        stage_time_display,
        recent_error_table,
    )
