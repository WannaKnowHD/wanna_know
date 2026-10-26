"""관리자 KB 현황 탭."""

import gradio as gr


def build_kb_tab():
    """KB 현황 화면 구성."""

    with gr.Tab("KB 현황"):
        gr.Markdown("### KB 현황")