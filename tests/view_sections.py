"""
view_sections.py : 매뉴얼 쪽 그림과 뽑힌 섹션 글자를 나란히 보여 주는 HTML 을 만든다

왼쪽  : PDF 쪽 그림 (표 = 파란 상자, 그림 = 빨간 상자)
오른쪽: 그 쪽에서 나온 섹션 (종류 · 제목 경로 · 글자)
→ 브라우저로 열어서 위아래로 넘기며 원본과 결과를 비교하면 된다.

실행 (프로젝트 맨 위 폴더에서):
    python scripts/view_sections.py data/raw/initial/hytrol_138acc_p1-27.pdf
결과:
    data/interim/check/view_<doc_id>.html  (data/interim 은 커밋하지 않는 폴더)
"""
import base64
import html
import os
import sys

import pymupdf

sys.path.insert(0, os.getcwd())
from chatbot.rag.document_processor import PdfExtractor

BLUE = (0, 0, 1)
RED = (1, 0, 0)
COLORS = {"text": "#555", "table": "#1f5fd6", "figure": "#d62f1f"}

STYLE = """
body { font-family: sans-serif; margin: 0; background: #f3f3f3; }
h1 { font-size: 18px; padding: 12px 16px; margin: 0; background: #222; color: #fff; }
.page { display: flex; gap: 16px; padding: 16px; border-bottom: 2px solid #ccc; }
.page img { width: 48%; border: 1px solid #aaa; background: #fff; }
.right { width: 52%; }
.label { font-weight: bold; margin-bottom: 8px; }
.sec { background: #fff; border-left: 5px solid #555; padding: 8px 10px; margin-bottom: 10px; }
.meta { font-size: 12px; color: #666; margin-bottom: 4px; }
pre { white-space: pre-wrap; font-size: 13px; margin: 0; }
.empty { color: #aaa; font-style: italic; }
table { border-collapse: collapse; font-size: 12px; width: 100%; }
td { border: 1px solid #bbb; padding: 3px 5px; vertical-align: top; }
tr:first-child td { background: #e8eefc; font-weight: bold; }
"""


def page_image(pdf_page, regions):
    """쪽 그림에 표·그림 상자를 그려 PNG(base64) 로 만든다."""
    for region in regions:
        color = BLUE if region["type"] == "table" else RED
        pdf_page.draw_rect(region["bbox"], color=color, width=1.5)
    png = pdf_page.get_pixmap(dpi=80).tobytes("png")
    return base64.b64encode(png).decode()


def table_html(text):
    """Markdown 표 글자를 HTML 표로 바꾼다. (표 위의 제목 줄은 그대로 글자로)"""
    title, rows = [], []
    for line in text.splitlines():
        if line.startswith("|---"):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            rows.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
        else:
            title.append(html.escape(line))
    head = f"<pre>{'<br>'.join(title)}</pre>" if title else ""
    return head + "<table>" + "".join(rows) + "</table>"


def main(pdf_path, sources_path="data/sources.csv", out_dir="data/interim/check"):
    ex = PdfExtractor(sources_path)
    pages = ex.extract_text(pdf_path)
    sections = ex.split_sections(pages)
    doc_id = pages[0].doc_id

    doc = pymupdf.open(pdf_path)
    parts = [f"<html><head><meta charset='utf-8'><title>{doc_id}</title><style>{STYLE}</style></head><body>",
             f"<h1>{html.escape(doc_id)} · {len(pages)}쪽 · 섹션 {len(sections)}개</h1>"]
    for page in pages:
        original = ex.to_original_page(doc_id, page.page_no)
        img = page_image(doc[page.page_no - 1], page.regions)
        items = []
        for s in sections:
            if s.page != original:
                continue
            if not s.text.strip():
                body = "<pre class='empty'>(글자 없음 · crop_regions 에서 채움)</pre>"
            elif s.type == "table":
                body = table_html(s.text)
            else:
                body = f"<pre>{html.escape(s.text)}</pre>"
            items.append(f"<div class='sec' style='border-color:{COLORS.get(s.type, '#555')}'>"
                         f"<div class='meta'><b>{s.type}</b> · {html.escape(s.section_path) or '(제목 없음)'}</div>"
                         f"{body}</div>")
        parts.append(f"<div class='page'><img src='data:image/png;base64,{img}'>"
                     f"<div class='right'><div class='label'>파일 p.{page.page_no} → 출처 p.{original}</div>"
                     f"{''.join(items) or '<div class=empty>(섹션 없음)</div>'}</div></div>")
    parts.append("</body></html>")

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"view_{doc_id}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print(f"저장: {out_path}  → 브라우저로 열어 보세요")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python scripts/view_sections.py <PDF 경로>")
        sys.exit(1)
    main(sys.argv[1])