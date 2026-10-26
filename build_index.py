"""실행: python build_index.py → KB·색인 전체 재생성"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Chunk, Section


class IndexBuilder:
    """IndexBuilder (기능명세서 기준 뼈대)."""

    def chunk_sections(self, sections: list[Section]):
        """
        섹션 청크 생성 (300~600토큰, 최대 800, 겹침 80), 문서명·모델명을 앞에 붙임, 청크 메타데이터에 chunk_type,
        image_path, page 추가. 표·그림은 쪼개지 않고 1개 청크로

        Returns: list[Chunk]
        """
        raise NotImplementedError("IndexBuilder.chunk_sections")

    def build_vector_index(self, items: list[Chunk | dict]):
        """
        KB·청크를 임베딩해 Chroma에 저장

        Returns: None
        """
        raise NotImplementedError("IndexBuilder.build_vector_index")

    def build_bm25_index(self, items: list[Chunk | dict]):
        """
        KB·청크로 BM25 색인 생성 후 파일로 저장

        Returns: None
        """
        raise NotImplementedError("IndexBuilder.build_bm25_index")

    def main(self):
        """
        명령 1개(python build_index.py)로 data/raw/의 전체 PDF를 문서 추출(PdfExtractor) → KB
        변환(KbBuilder) → KB 검증 → 청킹 → 색인 순서로 처음부터 다시 만듦 (청킹 설정·임베딩 모델을 바꿨을 때 사용)

        Returns: None
        """
        raise NotImplementedError("IndexBuilder.main")

    def add_document(self, pdf_path: str, meta: dict):
        """
        PDF 1건 추가 처리: 문서 추출(PdfExtractor) → KB 변환(KbBuilder) → KB 검증(validate_kb) → 청킹 →
        벡터 색인에 추가, BM25 색인은 다시 생성. 같은 파일명이 있으면 기존 청크를 지우고 다시 색인. 진행 상태(색인 중 → 완료/실패와 사유)를
        MySQL 문서 테이블에 기록

        Returns: bool, str
        """
        raise NotImplementedError("IndexBuilder.add_document")


if __name__ == "__main__":
    IndexBuilder().main()
