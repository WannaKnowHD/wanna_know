"""PDF 추출, KB 변환"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Page


class PdfExtractor:
    """PdfExtractor (기능명세서 기준 뼈대)."""

    def extract_text(self, pdf_path: str, pages: list[int] | None):
        """
        텍스트와 함께 페이지 안 표·그림 위치(bbox)도 반환 (Page에 regions 항목 추가)

        Returns: list[Page]
        """
        raise NotImplementedError("PdfExtractor.extract_text")

    def crop_regions(self, pdf_path: str, page: int, regions: list[dict]):
        """
        PDF의 표·그림 영역을 이미지로 잘라 비전 모델(LLMClient.vision)로 보내고, 표는 Markdown으로, 그림은 영어 설명으로 변환해
        data/interim/에 저장. 같은 영역은 캐시를 써서 다시 호출하지 않음. regions는 영역마다 종류(table/figure)와 bbox,
        결과는 영역마다 종류·변환 결과 텍스트·이미지 경로

        Returns: list[dict]
        """
        raise NotImplementedError("PdfExtractor.crop_regions")

    def split_sections(self, pages: list[Page]):
        """
        섹션 제목 기준으로 본문을 나눔. 표·그림 변환 결과도 섹션에 포함 (type: text/table/figure)

        Returns: list[Section]
        """
        raise NotImplementedError("PdfExtractor.split_sections")


class KbBuilder:
    """KbBuilder (기능명세서 기준 뼈대)."""

    def to_troubleshooting_kb(self, rows: list, doc_id: str, page: int):
        """
        트러블슈팅 표(증상 → 원인 → 조치)를 KB JSON으로 변환하고 원인마다 id(kb_id#c1…) 부여

        Returns: list[dict]
        """
        raise NotImplementedError("KbBuilder.to_troubleshooting_kb")

    def to_maintenance_kb(self, rows: list, doc_id: str, page: int):
        """
        점검표(일간·주간·월간·분기 또는 운전 시간)를 점검 KB로 변환

        Returns: list[dict]
        """
        raise NotImplementedError("KbBuilder.to_maintenance_kb")

    def merge_applies_to(self, kbs: list[dict]):
        """
        같은 표가 여러 모델에 반복되면 1건으로 합치고 applies_to에 모델 목록 기록

        Returns: list[dict]
        """
        raise NotImplementedError("KbBuilder.merge_applies_to")

    def validate_kb(self, kb: dict, source_text: str):
        """
        필수 항목, id 중복, 인용문이 원문의 부분 문자열인지 검사

        Returns: bool, list[str]
        """
        raise NotImplementedError("KbBuilder.validate_kb")
