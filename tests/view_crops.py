"""
view_crops.py : crop_regions 결과를 눈으로 확인하는 HTML 을 만든다

표·그림 하나마다 한 줄로
  [잘라 낸 이미지]  |  [비전 전 : PDF 에서 읽은 글자]  |  [비전 후 : Markdown 표 · 영어 설명]
을 나란히 보여 준다. 이미지와 비전 결과를 비교해서 빠진 칸·틀린 숫자·없는 부품 이름이 없는지 보면 된다.

실행 (프로젝트 맨 위 폴더에서):
    python tests/view_crops.py data/raw/initial/hytrol_138acc_p1-27.pdf            # 자르기만 (비용 0)
    python tests/view_crops.py data/raw/initial/hytrol_138acc_p1-27.pdf --vision   # 비전 모델까지 (OpenAI 호출)
    python tests/view_crops.py data/raw/initial/hytrol_138acc_p1-27.pdf --vision --pages 22 24
터미널에 단계별 시간(extract_text · crop_regions · 가장 느린 쪽)도 출력한다.
결과:
    data/interim/check/crops_<doc_id>.html  (data/interim 은 커밋하지 않는 폴더)
    --vision 결과는 data/interim/<doc_id>/*.json 에 캐시되어, 다시 실행하면 비전 호출 없이 바로 나온다.
"""
import argparse
import base64
import html
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from chatbot.rag.document_processor import PdfExtractor

STYLE = """
body { font-family: sans-serif; margin: 0; background: #f3f3f3; }
h1 { font-size: 18px; padding: 12px 16px; margin: 0; background: #222; color: #fff; }
.sum { padding: 8px 16px; background: #fff; border-bottom: 1px solid #ccc; font-size: 14px; }
.row { display: grid; grid-template-columns: 38% 24% 38%; gap: 12px; padding: 14px 16px;
       border-bottom: 1px solid #ccc; background: #fff; margin: 8px; }
.row img { width: 100%; border: 1px solid #aaa; }
.tag { font-weight: bold; margin-bottom: 6px; }
.table { color: #1f5fd6; } .figure { color: #d62f1f; }
.head { font-size: 12px; color: #666; margin-bottom: 4px; }
pre { white-space: pre-wrap; font-size: 12px; margin: 0; background: #fafafa; padding: 6px; }
table { border-collapse: collapse; font-size: 12px; width: 100%; }
td { border: 1px solid #bbb; padding: 3px 5px; vertical-align: top; }
tr:first-child td { background: #e8eefc; font-weight: bold; }
.same { color: #b26a00; font-weight: bold; }
.empty { color: #aaa; font-style: italic; }
"""


def markdown_html(text):
    """Markdown 표가 있으면 HTML 표로, 나머지 줄(Summary 등)은 글자로 보여 준다."""
    rows, other = [], []
    for line in text.splitlines():
        if line.startswith(("|---", "| ---")):
            continue
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
        elif line.strip():
            other.append(html.escape(line))
    table = f"<table>{''.join(rows)}</table>" if rows else ""
    note = f"<pre>{'<br>'.join(other)}</pre>" if other else ""
    return table + note or "<span class='empty'>(글자 없음)</span>"


def image_tag(path):
    with open(path, "rb") as f:
        return f"<img src='data:image/png;base64,{base64.b64encode(f.read()).decode()}'>"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--vision", action="store_true", help="비전 모델 호출 (OpenAI 비용 발생)")
    parser.add_argument("--pages", type=int, nargs="*", help="PDF 파일 쪽 번호 (없으면 전체)")
    args = parser.parse_args()

    llm = None
    settings = None
    if args.vision:
        from chatbot.core.config import Settings
        from chatbot.rag.llm import LLMClient
        settings = Settings.load()
        llm = LLMClient(settings)
    interim_dir = str(settings.interim_dir) if settings else "data/interim"

    ex = PdfExtractor("data/sources.csv", llm=llm, interim_dir=interim_dir)
    t0 = time.perf_counter()
    pages = ex.extract_text(args.pdf, args.pages)
    extract_sec = time.perf_counter() - t0
    doc_id = pages[0].doc_id

    rows, count, changed, start = [], 0, 0, time.time()
    crop_sec, slowest = 0.0, (0.0, 0)
    for page in pages:
        before = page.regions
        t0 = time.perf_counter()
        after = ex.crop_regions(args.pdf, page.page_no, before)
        sec = time.perf_counter() - t0
        crop_sec += sec
        slowest = max(slowest, (sec, page.page_no))
        original = ex.to_original_page(doc_id, page.page_no)
        for i, (b, a) in enumerate(zip(before, after, strict=True), start=1):
            count += 1
            same = a["text"] == b["text"]
            changed += not same
            note = "<span class='same'>비전 결과 없음 (원래 글자 그대로)</span>" if same else "비전 결과"
            rows.append(
                f"<div class='row'>"
                f"<div><div class='tag {a['type']}'>{a['type']} · 파일 p.{page.page_no} → 출처 p.{original} · #{i}</div>"
                f"{image_tag(a['image_path'])}"
                f"<div class='head'>{html.escape(os.path.basename(a['image_path']))} · bbox "
                f"{tuple(round(v) for v in a['bbox'])}</div></div>"
                f"<div><div class='head'>비전 전 (PDF 글자)</div>{markdown_html(b['text'])}</div>"
                f"<div><div class='head'>비전 후 · {note}</div>{markdown_html(a['text'])}</div>"
                f"</div>")

    mode = "비전 모델 사용" if llm else "자르기만 (비전 없음)"
    summary = (f"{mode} · 표·그림 {count}개 · 비전 결과가 들어간 것 {changed}개 · "
               f"{time.time() - start:.1f}초 (캐시가 있으면 비전 호출 없이 빠르게 끝남)")
    page_html = (f"<html><head><meta charset='utf-8'><title>{doc_id} crops</title><style>{STYLE}</style>"
                 f"</head><body><h1>{html.escape(doc_id)} · crop_regions 확인</h1>"
                 f"<div class='sum'>{summary}</div>{''.join(rows)}</body></html>")

    out_dir = os.path.join("data", "interim", "check")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"crops_{doc_id}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page_html)
    print(summary)
    print(f"[시간] extract_text ({len(pages)}쪽)      : {extract_sec:6.2f}초  (쪽당 {extract_sec / len(pages):.2f}초)")
    print(f"[시간] crop_regions ({count}개)      : {crop_sec:6.2f}초  (영역당 {crop_sec / max(count, 1):.2f}초)")
    print(f"[시간] 가장 오래 걸린 쪽            : 파일 p.{slowest[1]} {slowest[0]:.2f}초")
    print(f"[시간] 전체                         : {extract_sec + crop_sec:6.2f}초")
    print("       (--vision 두 번째 실행부터는 캐시를 읽어서 비전 시간이 빠짐 → 처음 시간을 보려면 data/interim/<doc_id>/*.json 삭제)")
    print(f"저장: {out_path}  → 브라우저로 열어 보세요")


if __name__ == "__main__":
    main()