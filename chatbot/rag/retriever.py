"""하이브리드 검색, 리랭크"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Hit


class Retriever:
    """Retriever (기능명세서 기준 뼈대)."""

    def search(self, queries: list[str], equipment: dict | None, qtype: str):
        """
        검색 진입점: BM25·벡터 검색(설비 필터 적용) → RRF 결합 → 리랭킹 → 상위 5개 반환. 필터를 건 결과가 기준값에 못 미치면 필터 없이
        다시 검색하고 "다른 설비 매뉴얼 기준"으로 표시

        Returns: list[Hit]
        """
        raise NotImplementedError("Retriever.search")

    def bm25_search(self, query: str, k: int, equipment: dict | None):
        """
        BM25로 상위 20개 후보 검색 (설비 필터가 있으면 해당 설비 문서 안에서만 검색)

        Returns: list[Hit]
        """
        raise NotImplementedError("Retriever.bm25_search")

    def vector_search(self, query: str, k: int, equipment: dict | None):
        """
        벡터 유사도로 상위 20개 후보 검색 (설비 필터가 있으면 메타데이터 조건으로 해당 설비 문서만 검색)

        Returns: list[Hit]
        """
        raise NotImplementedError("Retriever.vector_search")

    def rrf_merge(self, lists: list[list[Hit]]):
        """
        두 검색 결과를 RRF(k=60)로 합침

        Returns: list[Hit]
        """
        raise NotImplementedError("Retriever.rrf_merge")

    def rerank(self, query: str, hits: list[Hit]):
        """
        리랭커(bge-reranker-v2-m3)로 후보를 다시 정렬

        Returns: list[Hit]
        """
        raise NotImplementedError("Retriever.rerank")

    def apply_equipment_filter(self, equipment: dict | None, hits: list[Hit] | None):
        """
        명판으로 정한 설비를 검색 단계에서 쓸 메타데이터 조건으로 만들고, 필터를 건 결과의 리랭커 1위 점수가 기준값보다 낮으면 필터를 풀지 판단

        Returns: dict | None, bool
        """
        raise NotImplementedError("Retriever.apply_equipment_filter")

    def is_not_found(self, hits: list[Hit]):
        """
        리랭커 1위 점수가 기준값(개발셋으로 결정, 예시 0.55)보다 낮으면 "확인되지 않음"으로 판단

        Returns: bool
        """
        raise NotImplementedError("Retriever.is_not_found")
