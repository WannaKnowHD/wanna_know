"""관리자 답변 품질 탭."""

import gradio as gr


def build_quality_tab():
    """답변 품질 화면 구성."""

    with gr.Tab("답변 품질"):
        gr.Markdown("### 답변 품질")