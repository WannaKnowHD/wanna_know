"""
crop_regions 테스트 (실제 OpenAI 호출 없음)

- 테스트용 PDF 를 PyMuPDF 로 직접 만들고, LLMClient 대신 가짜 LLM 을 넣는다.
실행: python -m pytest tests/crop_regions_test.py -v
"""
import json
import os

import pymupdf
import pytest

from chatbot.rag.document_processor import PdfExtractor


class FakeLLM:
    """LLMClient.vision 과 같은 모양으로 답하는 가짜 LLM"""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def vision(self, image, prompt, json_schema):
        self.calls.append((json_schema.__name__, prompt[:20]))
        if self.fail:
            return {"text": "", "parsed": None}
        if json_schema.__name__ == "TableResult":
            return {"text": "", "parsed": {"markdown": "| A | B |\n|---|---|\n| 1 | 2 |",
                                           "summary": "A test table."}}
        return {"text": "", "parsed": {"description": "A drawing of a sprocket."}}


def make_pdf(path):
    """1쪽: 선으로 그린 표 1개 + 도면 1개"""
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((40, 60), "Trouble Shooting", fontsize=15)
    x, y = [40, 200, 380, 560], [100, 125, 165]
    for xi in x:
        p.draw_line((xi, y[0]), (xi, y[-1]))
    for yi in y:
        p.draw_line((x[0], yi), (x[-1], yi))
    for xi, t in zip(x, ["TROUBLE", "CAUSE", "SOLUTION"], strict=False):
        p.insert_text((xi + 5, 117), t, fontsize=9)
    p.insert_text((45, 145), "Motor overheating.", fontsize=9)
    for i in range(12):                                   # 도면 = 선 여러 개
        p.draw_line((100 + i * 20, 250), (120 + i * 20, 450))
    p.draw_rect((100, 250, 360, 450))
    p.insert_text((150, 350), "SPROCKET", fontsize=7)
    doc.save(path)
    doc.close()


@pytest.fixture
def setup(tmp_path):
    pdf = tmp_path / "test_manual.pdf"
    make_pdf(str(pdf))
    return str(pdf), str(tmp_path / "interim")


def regions_of(ex, pdf):
    return ex.extract_text(pdf, [1])[0].regions


def test_vision_results_and_image_saved(setup):
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", llm=FakeLLM(), interim_dir=interim)
    out = ex.crop_regions(pdf, 1, regions_of(ex, pdf))
    table = next(r for r in out if r["type"] == "table")
    figure = next(r for r in out if r["type"] == "figure")
    assert table["text"].startswith("| A | B |") and "Summary: A test table." in table["text"]
    assert figure["text"] == "A drawing of a sprocket."
    for r in out:                                         # 잘라 낸 이미지가 data/interim/{doc_id}/ 에 있음
        assert os.path.exists(r["image_path"])
        assert r["image_path"].startswith(os.path.join(interim, "test_manual"))
        with open(r["image_path"], "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_cache_skips_second_call(setup):
    pdf, interim = setup
    llm = FakeLLM()
    ex = PdfExtractor(sources_path="", llm=llm, interim_dir=interim)
    regions = regions_of(ex, pdf)
    first = ex.crop_regions(pdf, 1, regions)
    calls = len(llm.calls)
    second = ex.crop_regions(pdf, 1, regions)              # 두 번째는 캐시
    assert len(llm.calls) == calls
    assert [r["text"] for r in first] == [r["text"] for r in second]


def test_cache_ignored_when_prompt_version_changes(setup):
    pdf, interim = setup
    llm = FakeLLM()
    ex = PdfExtractor(sources_path="", llm=llm, interim_dir=interim)
    regions = regions_of(ex, pdf)
    out = ex.crop_regions(pdf, 1, regions)
    cache = out[0]["image_path"].replace(".png", ".json")
    with open(cache, encoding="utf-8") as f:
        data = json.load(f)
    data["prompt_version"] = "old"
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f)
    calls = len(llm.calls)
    ex.crop_regions(pdf, 1, regions)
    assert len(llm.calls) == calls + 1                     # 버전이 다른 1건만 다시 부름


def test_failure_keeps_original_text(setup):
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", llm=FakeLLM(fail=True), interim_dir=interim)
    regions = regions_of(ex, pdf)
    out = ex.crop_regions(pdf, 1, regions)
    assert [r["text"] for r in out] == [r["text"] for r in regions]   # PDF 에서 읽은 글자 그대로
    assert not any(name.endswith(".json") for name in os.listdir(os.path.join(interim, "test_manual")))


def test_without_llm_only_crops(setup):
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", interim_dir=interim)
    regions = regions_of(ex, pdf)
    out = ex.crop_regions(pdf, 1, regions)
    assert all(os.path.exists(r["image_path"]) for r in out)
    assert [r["text"] for r in out] == [r["text"] for r in regions]


def test_sections_get_vision_text_and_image(setup):
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", llm=FakeLLM(), interim_dir=interim)
    pages = ex.extract_text(pdf)
    for page in pages:
        page.regions = ex.crop_regions(pdf, page.page_no, page.regions)
    sections = ex.split_sections(pages)
    figure = next(s for s in sections if s.type == "figure")
    assert figure.text == "A drawing of a sprocket." and figure.image_path.endswith(".png")


def test_right_prompt_and_format_per_type(setup):
    """표는 TABLE_TO_MARKDOWN + TableResult, 그림은 FIGURE_DESCRIBE + FigureResult 로 부른다."""
    pdf, interim = setup
    llm = FakeLLM()
    ex = PdfExtractor(sources_path="", llm=llm, interim_dir=interim)
    ex.crop_regions(pdf, 1, regions_of(ex, pdf))
    assert ("TableResult", "This is an OCR task") in [(s, p[:19]) for s, p in llm.calls]
    assert ("FigureResult", "Describe this figur") in [(s, p[:19]) for s, p in llm.calls]


def test_image_covers_box_with_padding(setup):
    """잘라 낸 이미지 크기 = (상자 + 여유 4pt) × 200dpi / 72"""
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", interim_dir=interim)
    regions = regions_of(ex, pdf)
    for region, out in zip(regions, ex.crop_regions(pdf, 1, regions), strict=True):
        x0, y0, x1, y1 = region["bbox"]
        pix = pymupdf.Pixmap(out["image_path"])
        assert abs(pix.width - (x1 - x0 + 8) * 200 / 72) <= 3
        assert abs(pix.height - (y1 - y0 + 8) * 200 / 72) <= 3


def test_cache_file_contents(setup):
    """캐시 json 에 상자 · 프롬프트 버전 · 결과 글자가 들어 있다."""
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", llm=FakeLLM(), interim_dir=interim)
    out = ex.crop_regions(pdf, 1, regions_of(ex, pdf))
    with open(out[0]["image_path"].replace(".png", ".json"), encoding="utf-8") as f:
        data = json.load(f)
    assert set(data) == {"bbox", "prompt_version", "text"}
    assert data["text"] == out[0]["text"]


def test_input_regions_not_changed(setup):
    """원래 regions 는 그대로 두고 새 목록을 돌려준다. (비교·재실행용)"""
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", llm=FakeLLM(), interim_dir=interim)
    regions = regions_of(ex, pdf)
    before = [dict(r) for r in regions]
    ex.crop_regions(pdf, 1, regions)
    assert regions == before


def test_no_regions(setup):
    pdf, interim = setup
    ex = PdfExtractor(sources_path="", llm=FakeLLM(), interim_dir=interim)
    assert ex.crop_regions(pdf, 1, []) == []
