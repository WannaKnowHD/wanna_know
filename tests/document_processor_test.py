"""
document_processor.py 테스트 (PdfExtractor.extract_text · split_sections)

- 테스트용 PDF 를 PyMuPDF 로 직접 만들어서 확인한다 (매뉴얼 PDF 없이도 돌아감)
- data/raw/initial/ 에 실제 매뉴얼이 있으면 그 파일로도 확인한다 (없으면 건너뜀)
실행: python -m pytest tests/document_processor_test.py -v
"""
import os

import pymupdf
import pytest

from chatbot.rag.document_processor import PdfExtractor

RAW = os.path.join("data", "raw", "initial")


# ------------------------------------------------------------------------------
# 테스트용 PDF 만들기
# ------------------------------------------------------------------------------
def make_pdf(path):
    """3쪽짜리 테스트 PDF
    1쪽 : 큰 제목 + 영어(왼쪽)·스페인어(오른쪽) 2단 + 아래 쪽 번호
    2쪽 : 작은 제목 + 표(선으로 그린 3칸 표) + 표 제목
    3쪽 : 도면(선 여러 개) + 이름표 + 본문
    """
    doc = pymupdf.open()

    # 1쪽
    p = doc.new_page(width=612, height=792)
    p.insert_text((40, 60), "Installation", fontsize=20)
    p.insert_text((40, 100), "Remove the chain guard before you", fontsize=10)
    p.insert_text((40, 114), "connect the conveyors together.", fontsize=10)
    p.insert_text((320, 100), "Remueva la guarda de la cadena para", fontsize=10)
    p.insert_text((320, 114), "conectar los transportadores con el", fontsize=10)
    p.insert_text((40, 160), "Check that the bolts are tight-", fontsize=10)
    p.insert_text((40, 174), "ened after one week.", fontsize=10)
    p.insert_text((320, 160), "Revise que los pernos de la base", fontsize=10)
    p.insert_text((300, 780), "6", fontsize=9)

    # 2쪽
    p = doc.new_page(width=612, height=792)
    p.insert_text((40, 60), "Trouble Shooting", fontsize=15)
    p.insert_text((40, 100), "The chart lists possible problems.", fontsize=10)
    p.insert_text((40, 140), "TROUBLE SHOOTING DRIVES", fontsize=10)
    x = [40, 200, 380, 560]
    y = [150, 175, 215]
    for xi in x:
        p.draw_line((xi, y[0]), (xi, y[-1]))
    for yi in y:
        p.draw_line((x[0], yi), (x[-1], yi))
    for xi, t in zip(x, ["TROUBLE", "CAUSE", "SOLUTION"], strict=False):
        p.insert_text((xi + 5, 167), t, fontsize=9)
    p.insert_text((45, 195), "Motor overheating.", fontsize=9)
    p.insert_text((205, 195), "1) Motor is overloaded.", fontsize=9)
    p.insert_text((385, 195), "1) Reduce the load.", fontsize=9)
    p.insert_text((40, 260), "Lubricate the chain every month.", fontsize=10)

    # 3쪽
    p = doc.new_page(width=612, height=792)
    p.insert_text((40, 60), "Chain Alignment", fontsize=15)
    p.insert_text((40, 100), "Align the sprockets as shown in the figure below.", fontsize=10)
    for i in range(12):                                   # 도면 = 선 여러 개
        p.draw_line((100 + i * 20, 150), (120 + i * 20, 350))
    p.draw_rect((100, 150, 360, 350))
    p.insert_text((150, 250), "SPROCKET", fontsize=7)
    p.insert_text((40, 420), "Tighten the set screws after alignment.", fontsize=10)

    doc.save(path)
    doc.close()


@pytest.fixture
def setup(tmp_path):
    pdf = tmp_path / "test_manual.pdf"
    make_pdf(str(pdf))
    sources = tmp_path / "sources.csv"
    sources.write_text("doc_id,file,page_map\ntest_doc,test_manual.pdf,1-3>21\n", encoding="utf-8")
    ex = PdfExtractor(str(sources))
    pages = ex.extract_text(str(pdf))
    return ex, pages, str(pdf)


# ------------------------------------------------------------------------------
# extract_text
# ------------------------------------------------------------------------------
def test_page_fields(setup):
    _, pages, _ = setup
    assert [p.page_no for p in pages] == [1, 2, 3]          # PDF 파일 쪽
    assert all(p.doc_id == "test_doc" for p in pages)       # sources.csv 에서 찾음


def test_spanish_column_removed(setup):
    _, pages, _ = setup
    assert "Remove the chain guard" in pages[0].text
    assert "Remueva" not in pages[0].text and "pernos" not in pages[0].text


def test_page_number_removed(setup):
    _, pages, _ = setup
    assert "6" not in pages[0].text.split()


def test_select_pages(setup):
    ex, _, pdf = setup
    assert [p.page_no for p in ex.extract_text(pdf, [2])] == [2]


def test_table_region(setup):
    _, pages, _ = setup
    tables = [r for r in pages[1].regions if r["type"] == "table"]
    assert len(tables) == 1
    assert "| TROUBLE | CAUSE | SOLUTION |" in tables[0]["text"]
    assert "Motor is overloaded" in tables[0]["text"]
    assert "Motor is overloaded" not in pages[1].text      # 표 안 글자는 본문에서 뺌
    assert len(tables[0]["bbox"]) == 4 and tables[0]["image_path"] is None


def test_figure_region(setup):
    _, pages, _ = setup
    figures = [r for r in pages[2].regions if r["type"] == "figure"]
    assert len(figures) == 1
    assert "SPROCKET" in figures[0]["text"]                  # 이름표는 그림 쪽으로
    assert "SPROCKET" not in pages[2].text
    assert "Tighten the set screws" in pages[2].text         # 본문은 그대로


def test_unknown_pdf_uses_file_name(tmp_path):
    pdf = tmp_path / "admin_added.pdf"
    make_pdf(str(pdf))
    pages = PdfExtractor(sources_path="").extract_text(str(pdf), [1])
    assert pages[0].doc_id == "admin_added"


# ------------------------------------------------------------------------------
# split_sections
# ------------------------------------------------------------------------------
def test_sections(setup):
    ex, pages, _ = setup
    sections = ex.split_sections(pages)
    paths = {s.section_path for s in sections}
    assert "Installation" in paths
    assert "Installation > Trouble Shooting" in paths       # 큰 제목 > 작은 제목
    assert {s.type for s in sections} == {"text", "table", "figure"}


def test_section_page_uses_page_map(setup):
    ex, pages, _ = setup
    sections = ex.split_sections(pages)
    assert {s.page for s in sections} == {21, 22, 23}       # 1-3>21


def test_table_section_has_title(setup):
    ex, pages, _ = setup
    table = next(s for s in ex.split_sections(pages) if s.type == "table")
    assert table.text.startswith("TROUBLE SHOOTING DRIVES\n|")


def test_order_and_hyphen(setup):
    ex, pages, _ = setup
    first = next(s for s in ex.split_sections(pages) if s.section_path == "Installation")
    assert first.text.index("Remove the chain") < first.text.index("Check that the bolts")
    assert "tightened after one week" in first.text          # tight- / ened → tightened


def test_text_after_table_stays_in_section(setup):
    ex, pages, _ = setup
    sections = [s for s in ex.split_sections(pages) if s.page == 22]
    assert [s.type for s in sections] == ["text", "table", "text"]
    assert "Lubricate the chain" in sections[-1].text


def test_page_map_parsing():
    ex = PdfExtractor(sources_path="")
    ex.page_maps = {"a": "1-24>6;25-31>54", "b": ""}
    assert ex.to_original_page("a", 1) == 6
    assert ex.to_original_page("a", 24) == 29
    assert ex.to_original_page("a", 25) == 54
    assert ex.to_original_page("b", 7) == 7


def test_spanish_detection():
    ex = PdfExtractor(sources_path="")
    assert ex.is_spanish("Remueva las catarinas y la cubierta de la guarda")
    assert ex.is_spanish("Instalación de los soportes")
    assert not ex.is_spanish("Remove sprockets and chain guard back plate")
    assert not ex.is_spanish("Model 138-ACC")


# ------------------------------------------------------------------------------
# 실제 매뉴얼 (data/raw/initial/ 에 있을 때만)
# ------------------------------------------------------------------------------
HYTROL = os.path.join(RAW, "hytrol_138acc_p1-27.pdf")


@pytest.mark.skipif(not os.path.exists(HYTROL), reason="매뉴얼 PDF 없음")
def test_real_hytrol_troubleshooting():
    ex = PdfExtractor(os.path.join("data", "sources.csv"))
    pages = ex.extract_text(HYTROL, [22])
    sections = ex.split_sections(pages)
    tables = [s for s in sections if s.type == "table"]
    assert len(tables) == 2
    assert "Motor is overloaded" in tables[0].text
    assert all(s.page == 22 for s in sections)
    assert all(s.section_path == "Trouble Shooting" for s in sections)