"""관리자 KB 현황 탭 (SCR-08, WBS 3.8.6 · 기능 M-06)

Figma 08: 제목("현재 청킹된 PDF N건") + PDF 추가하기 버튼 / 표(파일명 · 페이지 · 청크 수 · 색인 일시 · 상태 배지).
실제 처리는 chatbot/ui/admin/kb_files.py, 버튼 연결은 AdminDashboard._wire_kb_tab 이 맡는다.
스타일은 admin.css(공통)를 고치지 않고 이 탭 전용(KB_TAB_CSS)으로 둔다. 색은 admin.css 의 관리자 색 변수를 그대로 씀.
"""

from dataclasses import dataclass
from html import escape

import gradio as gr
import pandas as pd

from chatbot.ui.admin.kb_files import KB_STATUS_COLUMNS, STATUS_LABELS

# 색인 중일 때 목록을 자동으로 다시 읽는 간격(초)
REFRESH_SECONDS = 5

# 상태 글자 → 배지 모양 (완료: 흰 바탕 테두리 / 색인 중: 초록 채움 / 실패: 연한 빨강)
_BADGE_CLASSES = {STATUS_LABELS["done"]: "kb-badge-done", STATUS_LABELS["indexing"]: "kb-badge-running",
                  STATUS_LABELS["failed"]: "kb-badge-failed"}

# KB 현황 탭 전용 스타일 (#admin-kb-tab 안에서만 적용). 색은 admin.css 의 --admin-* 변수
KB_TAB_CSS = """<style>
#admin-kb-tab .kb-style { display: none !important; }
#admin-kb-tab .kb-head-row { align-items: flex-start !important; gap: 1rem !important; }
#admin-kb-tab .kb-title { margin: 0; color: var(--admin-text); font-size: 1.0625rem; font-weight: 700; }
#admin-kb-tab .kb-subtitle { margin: 0.25rem 0 0; color: var(--admin-text-muted); font-size: 0.75rem; }
#admin-kb-tab .kb-progress { margin: 0.5rem 0 0; color: var(--admin-accent); font-size: 0.75rem; font-weight: 600; }
#admin-kb-tab .kb-add-button { flex: 0 0 auto !important; min-width: 7.5rem !important; background: var(--admin-accent) !important;
  color: #ffffff !important; border: none !important; border-radius: 0.5rem !important; font-weight: 600 !important; }
#admin-kb-tab .kb-add-button:disabled { opacity: 0.55; cursor: not-allowed; }
#admin-kb-tab .kb-card { background: var(--admin-surface); border: 1px solid var(--admin-border); border-radius: 0.75rem;
  padding: 1rem 1rem 0.5rem; overflow-x: auto; }
#admin-kb-tab .kb-table { width: 100%; border-collapse: collapse; border: none !important; font-size: 0.8125rem;
  color: var(--admin-text); }
#admin-kb-tab .kb-table tr, #admin-kb-tab .kb-table th, #admin-kb-tab .kb-table td { border: none !important; }
#admin-kb-tab .kb-table th { background: var(--admin-table-head); color: var(--admin-text-muted); font-weight: 500;
  text-align: left; padding: 0.625rem 0.75rem; white-space: nowrap; }
#admin-kb-tab .kb-table td { padding: 0.8125rem 0.75rem; border-top: 1px solid var(--admin-border) !important;
  vertical-align: middle; }
#admin-kb-tab .kb-table .kb-num { width: 6rem; }
#admin-kb-tab .kb-table .kb-time { width: 8rem; white-space: nowrap; }
#admin-kb-tab .kb-table .kb-state { width: 7rem; }
#admin-kb-tab .kb-reason { display: block; margin-top: 0.25rem; color: #b42318; font-size: 0.75rem; word-break: break-all; }
#admin-kb-tab .kb-empty { padding: 1.5rem 0.75rem; color: var(--admin-text-muted); text-align: center; }
#admin-kb-tab .kb-badge { display: inline-block; padding: 0.25rem 0.875rem; border-radius: 999px; font-size: 0.75rem;
  white-space: nowrap; }
#admin-kb-tab .kb-badge-done { background: var(--admin-surface); border: 1px solid var(--admin-border); color: var(--admin-text); }
#admin-kb-tab .kb-badge-running { background: var(--admin-accent); color: #ffffff; }
#admin-kb-tab .kb-badge-failed { background: #fdecea; color: #b42318; }
body.dark #admin-kb-tab .kb-badge-failed { background: #3a1d1b; color: #f4a39a; }
body.dark #admin-kb-tab .kb-reason { color: #f4a39a; }
</style>"""


@dataclass
class KbTab:
    """KB 현황 탭에서 버튼·목록에 연결하는 컴포넌트."""

    tab: gr.Tab
    header: gr.HTML
    table: gr.HTML
    add_button: gr.UploadButton
    timer: gr.Timer

    def outputs(self) -> list:
        """
        목록을 다시 그릴 때 바뀌는 컴포넌트 (AdminDashboard._wire_kb_tab 의 outputs 순서)

        Returns: list (header, table, timer, add_button)
        """
        return [self.header, self.table, self.timer, self.add_button]


def render_kb_header(count: int, progress: str = "") -> str:
    """
    제목줄 HTML: "현재 청킹된 PDF N건" + 설명 + (색인 중이면) 진행 안내

    받는 것: 목록의 PDF 수, KbIndexer.progress_text() (색인 중이 아니면 빈 글자)
    Returns: str (HTML)
    """
    progress_html = f"<p class='kb-progress'>{escape(progress)}</p>" if progress else ""
    return (
        f"<p class='kb-title'>현재 청킹된 PDF {count}건</p>"
        "<p class='kb-subtitle'>kb/ 폴더의 문서 · 추가하면 색인이 갱신됩니다</p>"
        f"{progress_html}"
    )


def render_kb_table(table: pd.DataFrame) -> str:
    """
    KB 현황 표 HTML (파일명 · 페이지 · 청크 수 · 색인 일시 · 상태 배지). 실패한 행은 파일명 아래에 실패 사유

    받는 것: AdminDashboard.kb_status 결과 (열: KB_STATUS_COLUMNS)
    Returns: str (HTML). 파일명·사유는 escape 해서 넣음 (업로드 파일 이름이 그대로 화면에 나오므로)
    """
    head = "".join(
        f"<th class='{cls}'>{name}</th>"
        for name, cls in zip(KB_STATUS_COLUMNS[:5], ["", "kb-num", "kb-num", "kb-time", "kb-state"])
    )
    rows = []
    for row in table.itertuples(index=False):
        filename, pages, chunks, indexed_at, status, reason = row
        reason_html = f"<span class='kb-reason'>{escape(str(reason))}</span>" if reason else ""
        badge = _BADGE_CLASSES.get(status, "kb-badge-done")
        rows.append(
            "<tr>"
            f"<td>{escape(str(filename))}{reason_html}</td>"
            f"<td>{_cell(pages)}</td>"
            f"<td>{_cell(chunks)}</td>"
            f"<td>{_cell(indexed_at)}</td>"
            f"<td><span class='kb-badge {badge}'>{escape(str(status))}</span></td>"
            "</tr>"
        )
    body = "".join(rows) or "<tr><td class='kb-empty' colspan='5'>아직 색인된 PDF 가 없습니다.</td></tr>"
    return f"<div class='kb-card'><table class='kb-table'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _cell(value) -> str:
    """빈 값(None · NaN · 빈 글자)은 "—", 숫자는 정수로 표시."""
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return escape(str(value))


def build_kb_tab() -> KbTab:
    """
    KB 현황 화면 구성 (제목줄 · PDF 추가하기 버튼 · 문서 표). 값은 AdminDashboard.kb_status · add_pdf 결과로 채움

    Returns: KbTab
    """
    with gr.Tab("KB 현황", elem_id="admin-kb-tab") as tab:
        # 스타일만 담은 칸. 화면에서 숨겨서(.kb-style) 탭 바와 제목 사이에 빈 여백이 생기지 않게 한다
        gr.HTML(KB_TAB_CSS, elem_classes=["kb-style"])
        with gr.Row(elem_classes=["kb-head-row"]):
            header = gr.HTML(render_kb_header(0), padding=False)
            # 누르면 파일 선택 창이 바로 열림. type="filepath": 업로드한 파일 이름을 알 수 있다
            add_button = gr.UploadButton("PDF 추가하기", file_types=[".pdf"], file_count="single", type="filepath",
                                         scale=0, elem_classes=["kb-add-button"])
        table = gr.HTML(render_kb_table(pd.DataFrame(columns=KB_STATUS_COLUMNS)))
        # 색인 중일 때만 켜서 목록 상태(색인 중 → 완료/실패)를 자동으로 갱신
        timer = gr.Timer(REFRESH_SECONDS, active=False)

    return KbTab(tab, header, table, add_button, timer)
