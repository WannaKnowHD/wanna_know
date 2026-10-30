"""원문 보기의 "쪽 전체 보기" 이미지: 출처(doc_id · 인쇄 쪽 번호)의 매뉴얼 PDF 한 쪽을 그림 파일로 그려 둔다.

그린 파일은 PAGE_IMAGE_DIR 에 남겨 두고 다시 그리지 않는다. 채팅창은 이 파일을 /gradio_api/file= 주소로 읽으므로
ChatApp.launch_options 가 PAGE_IMAGE_DIR 을 allowed_paths 에 넣는다. 화면 모양은 messages.py 의 _render_sources.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import pymupdf

from chatbot.core.config import BASE_DIR
from chatbot.ui.admin.kb_files import document_info, list_pdfs, read_sources

logger = logging.getLogger(__name__)

RAW_DIR = BASE_DIR / "data" / "raw"
SOURCES_PATH = BASE_DIR / "data" / "sources.csv"
PAGE_IMAGE_DIR = BASE_DIR / "data" / "interim" / "page_images"   # .gitignore 의 data/interim/* 대상
PAGE_ZOOM = 2.0          # PDF 1pt → 2px (A4 한 쪽이 약 1190x1680px, 눌러서 크게 볼 때 글자가 또렷하게)
PAGE_JPEG_QUALITY = 80


def page_image_url(doc_id: str, page: int) -> str | None:
    """출처 한 건의 쪽 이미지 주소. PDF 를 찾지 못하거나 그 쪽이 없으면 None (원문 보기에서 쪽 전체 보기를 빼면 됨)"""
    path = _page_image(doc_id, page)
    return f"/gradio_api/file={path.as_posix()}" if path else None


@lru_cache(maxsize=256)
def _page_image(doc_id: str, page: int) -> Path | None:
    """doc_id 의 인쇄 쪽 page 를 그린 JPEG 경로. 이미 그려 둔 파일이 있으면 그대로 씀"""
    target = PAGE_IMAGE_DIR / f"{doc_id}_p{page}.jpg"
    if target.exists():
        return target
    found = _find_pdf(doc_id)
    if found is None:
        return None
    pdf_path, page_map = found
    page_no = to_file_page(page, page_map)
    try:
        with pymupdf.open(pdf_path) as doc:
            if not 1 <= page_no <= doc.page_count:
                return None
            pixmap = doc[page_no - 1].get_pixmap(matrix=pymupdf.Matrix(PAGE_ZOOM, PAGE_ZOOM))
            PAGE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            target.write_bytes(pixmap.tobytes("jpg", jpg_quality=PAGE_JPEG_QUALITY))
    except Exception:   # noqa: BLE001 — 깨진 PDF 등: 원문 보기에서 쪽 이미지만 빠지고 카드는 그대로
        logger.exception("출처 쪽 이미지를 만들지 못함: %s p.%s", doc_id, page)
        return None
    return target


def _find_pdf(doc_id: str) -> tuple[Path, str] | None:
    """doc_id 의 PDF 경로와 sources.csv page_map. doc_id 규칙은 KB 현황(kb_files.document_info)과 같음"""
    sources = read_sources(SOURCES_PATH)
    for pdf_path in list_pdfs(RAW_DIR):
        if document_info(pdf_path, RAW_DIR, sources)["doc_id"] == doc_id:
            return pdf_path, sources.get(pdf_path.name, {}).get("page_map", "")
    return None


def to_file_page(page: int, page_map: str) -> int:
    """인쇄 쪽 번호 → PDF 파일 쪽 (document_processor.PdfExtractor.to_original_page 의 반대).

    page_map 예) "1-19>21"         : 파일 1~19쪽 = 인쇄 21쪽부터 → 인쇄 23쪽은 파일 3쪽
                 "1-24>6;25-31>54" : 파일 1~24쪽 = 인쇄 6쪽부터, 25~31쪽 = 인쇄 54쪽부터
                 ""                : 쪽 번호 그대로
    """
    for part in (page_map or "").split(";"):
        m = re.fullmatch(r"\s*(\d+)-(\d+)>(\d+)\s*", part)
        if m:
            start, end, original_start = map(int, m.groups())
            if original_start <= page <= original_start + (end - start):
                return start + (page - original_start)
    return page
