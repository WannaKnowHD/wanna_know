"""관리자 KB 현황 탭 (SCR-08, WBS 3.8.6 · 기능 M-06)

청킹된 PDF 목록을 보여 주고, "PDF 추가하기"로 올린 매뉴얼을 색인한다.
실제 처리는 chatbot/ui/admin/kb_files.py, 버튼 연결은 AdminDashboard._wire_kb_tab 이 맡는다.
"""

from dataclasses import dataclass

import gradio as gr

from chatbot.ui.admin.kb_files import KB_STATUS_COLUMNS, MAX_PDF_BYTES

# 색인 중일 때 목록을 자동으로 다시 읽는 간격(초)
REFRESH_SECONDS = 5


@dataclass
class KbTab:
    """KB 현황 탭에서 버튼·목록에 연결하는 컴포넌트."""

    tab: gr.Tab
    progress: gr.Markdown
    table: gr.Dataframe
    refresh_button: gr.Button
    upload: gr.File
    add_button: gr.Button
    message: gr.Markdown
    timer: gr.Timer


def build_kb_tab() -> KbTab:
    """
    KB 현황 화면 구성 (청킹된 PDF 목록 · 목록 새로고침 · PDF 추가하기).
    값은 AdminDashboard.kb_status · add_pdf 결과로 채움

    Returns: KbTab
    """
    with gr.Tab("KB 현황", elem_id="admin-kb-tab") as tab:
        gr.Markdown("### KB 현황")
        with gr.Row(equal_height=True):
            gr.Markdown("챗봇이 참고하는 매뉴얼 PDF 목록입니다.", elem_classes=["admin-note"])
            refresh_button = gr.Button("목록 새로고침", size="sm", scale=0, elem_classes=["admin-toolbar-button"])
        # 색인 중일 때만 "⏳ 'x.pdf' 색인 중… (18:30 시작, 3분째)" 를 보여 줌 (KbIndexer.progress_text)
        progress = gr.Markdown()
        table = gr.Dataframe(
            headers=KB_STATUS_COLUMNS,
            value=[],
            interactive=False,
            wrap=True,
        )

        gr.Markdown("#### PDF 추가하기")
        gr.Markdown(
            f"글자가 들어 있는 PDF 만 추가할 수 있습니다 (스캔본·암호 PDF 제외, {MAX_PDF_BYTES // (1024 * 1024)}MB 이하). "
            "추가하면 색인을 다시 만들며, 그동안 챗봇은 기존 매뉴얼로 답합니다.",
            elem_classes=["admin-note"],
        )
        # type="filepath": 업로드한 파일 이름을 알 수 있다 (binary 는 내용만 옴)
        upload = gr.File(label="매뉴얼 PDF", file_types=[".pdf"], file_count="single", type="filepath")
        add_button = gr.Button("PDF 추가하기", variant="primary")
        message = gr.Markdown()
        # 색인 중일 때만 켜서 목록 상태(색인 중 → 완료/실패)를 자동으로 갱신
        timer = gr.Timer(REFRESH_SECONDS, active=False)

    return KbTab(tab, progress, table, refresh_button, upload, add_button, message, timer)
