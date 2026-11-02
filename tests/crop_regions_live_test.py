"""
crop_regions 실제 API 테스트 (OpenAI 비전 모델을 진짜로 부른다 · 비용 발생)

- Hytrol 매뉴얼의 표 2개(인쇄 p.22)와 그림 2개(p.19)를 잘라 비전 모델로 읽는다.
- 결과를 data/interim/check/live_crops.html 로 저장 → 브라우저로 열어 이미지와 비교한다.
- 매번 새로 부르도록 캐시는 pytest 임시 폴더에 둔다. (4번 호출)
- 단계별 걸린 시간(extract_text · 쪽별 crop_regions · 영역당 평균)을 터미널에 출력한다. (-s 옵션 필요)

실행 (환경변수 LIVE_VISION=1 일 때만 돈다. 평소 pytest 에서는 건너뜀):
    Windows cmd : set "LIVE_VISION=1" && python -m pytest tests/crop_regions_live_test.py -v -s
    Git Bash    : LIVE_VISION=1 python -m pytest tests/crop_regions_live_test.py -v -s
"""
import base64
import html
import os
import time

import pytest

from chatbot.rag.document_processor import PdfExtractor

HYTROL = os.path.join("data", "raw", "initial", "hytrol_138acc_p1-27.pdf")
OUT = os.path.join("data", "interim", "check", "live_crops.html")

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_VISION", "").strip() != "1" or not os.path.exists(HYTROL),
    reason="실제 API 테스트는 LIVE_VISION=1 이고 매뉴얼 PDF 가 있을 때만")


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """p.19 그림 2개, p.22 표 2개를 실제 비전 모델로 읽은 결과 (모듈에서 1번만 호출)"""
    from chatbot.core.config import Settings
    from chatbot.rag.llm import LLMClient

    interim = str(tmp_path_factory.mktemp("interim"))
    ex = PdfExtractor(os.path.join("data", "sources.csv"), llm=LLMClient(Settings.load()), interim_dir=interim)
    rows, times = [], []

    start = time.perf_counter()
    pages = ex.extract_text(HYTROL, [19, 22])
    extract_sec = time.perf_counter() - start

    for page in pages:
        start = time.perf_counter()
        after = ex.crop_regions(HYTROL, page.page_no, page.regions)
        times.append((page.page_no, len(page.regions), time.perf_counter() - start))
        rows += [(page.page_no, b, a) for b, a in zip(page.regions, after, strict=True)]
    print_times(extract_sec, times)
    save_html(rows)
    return rows


def print_times(extract_sec, times):
    """터미널에 단계별 시간 출력 (캐시 없이 매번 비전 호출한 시간)"""
    total_regions = sum(n for _, n, _ in times)
    total_crop = sum(t for _, _, t in times)
    print(f"\n[시간] extract_text (2쪽)          : {extract_sec:6.2f}초")
    for page_no, n, sec in times:
        avg = sec / n if n else 0
        print(f"[시간] crop_regions p.{page_no:<3} ({n}개)    : {sec:6.2f}초  (영역당 {avg:.2f}초)")
    avg = total_crop / total_regions if total_regions else 0
    print(f"[시간] crop_regions 합계 ({total_regions}개)     : {total_crop:6.2f}초  (영역당 {avg:.2f}초)")
    print(f"[시간] 전체                         : {extract_sec + total_crop:6.2f}초")


def save_html(rows):
    """이미지 | 비전 전 | 비전 후 를 나란히 저장"""
    style = ("body{font-family:sans-serif} .r{display:grid;grid-template-columns:40% 25% 35%;gap:10px;"
             "border-bottom:1px solid #ccc;padding:10px} img{width:100%;border:1px solid #aaa}"
             "pre{white-space:pre-wrap;font-size:12px;background:#f6f6f6;padding:6px}")
    parts = [f"<html><head><meta charset='utf-8'><style>{style}</style></head><body>"]
    for page_no, before, after in rows:
        with open(after["image_path"], "rb") as f:
            img = base64.b64encode(f.read()).decode()
        parts.append(f"<div class='r'><div><b>{after['type']} · p.{page_no}</b><br>"
                     f"<img src='data:image/png;base64,{img}'></div>"
                     f"<div><b>비전 전</b><pre>{html.escape(before['text'])}</pre></div>"
                     f"<div><b>비전 후</b><pre>{html.escape(after['text'])}</pre></div></div>")
    parts.append("</body></html>")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print(f"\n[확인] {OUT} 를 브라우저로 열어 이미지와 결과를 비교하세요")


def test_tables_become_markdown(results):
    tables = [a for _, b, a in results if a["type"] == "table"]
    assert len(tables) == 2
    for t in tables:
        lines = t["text"].splitlines()
        assert sum(ln.strip().startswith("|") for ln in lines) >= 3     # 머리글 + 구분선 + 행
        assert "Summary:" in t["text"]
        assert "TROUBLE" in t["text"].upper() and "CAUSE" in t["text"].upper()


def test_table_keeps_manual_wording(results):
    """비전 결과에 매뉴얼 원문 문장이 그대로 있어야 한다 (번역·요약 금지)"""
    first = next(a for _, b, a in results if a["type"] == "table")
    assert "Motor is overloaded" in first["text"]
    assert "Check for overloading of conveyor" in first["text"]


def test_figures_become_english_description(results):
    figures = [a for _, b, a in results if a["type"] == "figure"]
    assert len(figures) == 2
    for f in figures:
        assert len(f["text"]) > 30                                       # 이름표만이 아니라 설명 문장
        assert not any("가" <= c <= "힣" for c in f["text"])     # 한글 없음 (영어 설명)
    assert "FIGURE 19A" in figures[0]["text"].upper()                    # 그림 번호부터 시작하라는 프롬프트 규칙


def test_every_region_got_vision_result(results):
    """모든 영역에 비전 결과가 들어감 (거부·실패 없음)"""
    failed = [(p, a["type"]) for p, b, a in results if a["text"] == b["text"]]
    assert failed == []