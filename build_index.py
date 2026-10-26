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
        data/raw/의 모든 PDF로 색인을 처음부터 다시 만듦. 새 PDF만 추출·KB 변환하고 기존 문서는 캐시 재사용 → 청킹 →
        벡터·BM25 색인 생성. 새 색인은 임시 위치에 만든 뒤 완성되면 교체. 개발자 실행(python build_index.py)과 관리자 PDF
        추가에서 같이 사용. 재생성이 이미 돌고 있으면 끝날 때까지 기다림(동시에 두 번 실행하지 않음). 실패하면 기존 색인을 그대로 둠

        Returns: None
        """
        raise NotImplementedError("IndexBuilder.main")


if __name__ == "__main__":
    IndexBuilder().main()
