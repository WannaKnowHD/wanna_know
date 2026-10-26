"""관리자 시스템 현황 탭."""

import gradio as gr


def build_system_tab():
    """시스템 현황 화면 구성."""

    with gr.Tab("시스템"):
        gr.Markdown("### 시스템 현황")