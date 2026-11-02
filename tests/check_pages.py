"""
check_pages.py : extract_text · split_sections 결과를 PDF 위에 그려서 눈으로 확인하는 스크립트

각 쪽 맨 위에 "파일 p.N → 출처 p.M" 을 빨간 글씨로 찍고,
표는 파란 상자, 그림은 빨간 상자로 표시한 확인용 PDF 를 만든다.
→ 빨간 글씨의 "출처 p.M" 과 매뉴얼 아래에 인쇄된 쪽 번호가 같은지 보면 된다.

실행 (프로젝트 맨 위 폴더에서):
    python scripts/check_pages.py data/raw/initial/fenner_belt_p21-39.pdf
결과:
    data/interim/check/check_<doc_id>.pdf  (data/interim 은 커밋하지 않는 폴더)
"""
import os
import sys

import pymupdf

sys.path.insert(0, os.getcwd())
from chatbot.rag.document_processor import PdfExtractor

BLUE = (0, 0, 1)
RED = (1, 0, 0)


def main(pdf_path, sources_path="data/sources.csv", out_dir="data/interim/check"):
    ex = PdfExtractor(sources_path)
    pages = ex.extract_text(pdf_path)
    sections = ex.split_sections(pages)
    doc_id = pages[0].doc_id

    doc = pymupdf.open(pdf_path)
    for page in pages:
        pdf_page = doc[page.page_no - 1]
        original = ex.to_original_page(doc_id, page.page_no)

        # (1) 맨 위에 쪽 번호 찍기
        label = f"file p.{page.page_no} -> source p.{original}"
        pdf_page.insert_text((20, 20), label, fontsize=14, color=RED)

        # (2) 표 = 파란 상자, 그림 = 빨간 상자
        for region in page.regions:
            color = BLUE if region["type"] == "table" else RED
            pdf_page.draw_rect(region["bbox"], color=color, width=1.5)
            pdf_page.insert_text((region["bbox"][0], region["bbox"][1] - 2),
                                 region["type"], fontsize=8, color=color)

        # (3) 그 쪽의 섹션 제목을 왼쪽 아래에 적기
        paths = []
        for s in sections:
            if s.page == original and s.section_path not in paths:
                paths.append(s.section_path)
        for i, path in enumerate(paths[:4]):
            pdf_page.insert_text((20, pdf_page.rect.height - 40 + i * 9),
                                 "section: " + path[:110], fontsize=7, color=RED)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"check_{doc_id}.pdf")
    doc.save(out_path)
    print(f"저장: {out_path}  ({len(pages)}쪽, 섹션 {len(sections)}개)")
    for page in pages:
        print(f"  파일 p.{page.page_no:>2} → 출처 p.{ex.to_original_page(doc_id, page.page_no)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python scripts/check_pages.py <PDF 경로>")
        sys.exit(1)
    main(sys.argv[1])