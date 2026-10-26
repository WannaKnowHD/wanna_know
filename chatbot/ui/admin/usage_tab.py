"""관리자 운영 현황 탭."""

import gradio as gr


def build_usage_tab():
    """운영 현황 화면 구성."""

    with gr.Tab("운영 현황"):
        gr.Markdown("### 운영 현황")