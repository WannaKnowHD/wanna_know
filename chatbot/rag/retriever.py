"""하이브리드 검색, 리랭크

BM25 · 벡터(Chroma) 검색 → RRF(k=60) 결합 → 리랭커(bge-reranker-v2-m3) → 상위 5개.
설비 필터를 건 결과가 기준값에 못 미치면 필터 없이 다시 찾는다 (WBS 3.4.3~3.4.5).

색인 약속 (build_index.py와 맞출 것)
  - Chroma: settings.chroma_dir 폴더, 컬렉션 이름 COLLECTION_NAME, 벡터는 LLMClient.embed
  - BM25: settings.bm25_path(pickle)에 항목 목록 [{"id", "text", "metadata"}]을 저장한다.
    점수 표(BM25Okapi)는 Retriever가 불러올 때 tokenize로 만든다 (토큰 규칙을 한곳에 둠)
  - metadata (Chroma와 BM25가 같은 값):
      doc_id(str), page(int), chunk_type("kb" | "text" | "table" | "figure")
        text · table · figure는 문서 추출의 Section.type 값 그대로
      kb_type("troubleshooting" | "maintenance", KB만)
      all_models(bool): KB applies_to가 ["*"]이거나 청크 equipment_model이 "*"이면 True.
                        설비 필터를 걸어도 항상 검색 대상에 남는다 (schemas.ALL_MODELS)
"""

from __future__ import annotations

import logging
import pickle
import re
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import chromadb
from rank_bm25 import BM25Okapi

from chatbot.core.schemas import Hit

if TYPE_CHECKING:
    from chatbot.core.config import Settings
    from chatbot.rag.llm import LLMClient

logger = logging.getLogger(__name__)

COLLECTION_NAME = "manuals"
RRF_K = 60  # 기획서 8.3
CANDIDATE_K = 20  # BM25 · 벡터 검색마다 가져오는 후보 수
RERANK_POOL_SIZE = 20  # 리랭커에 넣는 후보 수. 리랭킹이 느리면 줄인다 (기획서 14.2)
TOP_K = 5
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

_WORD_PATTERN = re.compile(r"[a-z0-9]+|[가-힣]+")


class Reranker(Protocol):
    """(질문, 문서) 쌍마다 0~1 관련도를 주는 리랭커. 테스트에서는 가짜로 바꿔 끼운다."""

    def score(self, query: str, texts: list[str]) -> list[float]: ...


def tokenize(text: str) -> list[str]:
    """BM25 토큰으로 나눈다. 영문·숫자는 단어 그대로, 한글은 2글자씩 끊는다.

    한글은 조사가 붙어("모터가", "모터를") 단어가 그대로 맞지 않으므로 2글자 조각으로
    비교한다. 예: "138-ACC 모터가" → ["138", "acc", "모터", "터가"]
    """
    tokens: list[str] = []
    for word in _WORD_PATTERN.findall(text.lower()):
        if word.isascii() or len(word) <= 2:
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def load_bm25_items(path: str | Path) -> list[dict]:
    """build_index.py가 저장한 BM25 항목 목록을 읽는다."""
    # pickle은 우리가 만든 색인 파일에만 쓴다 (밖에서 받은 파일을 열면 코드가 실행될 수 있음)
    with open(path, "rb") as file:
        return list(pickle.load(file))


class BgeReranker:
    """bge-reranker-v2-m3로 (질문, 문서) 쌍마다 0~1 관련도를 매긴다.

    처음 score를 부를 때 모델을 불러온다 (첫 실행은 약 2.3GB를 내려받음). CrossEncoder는
    출력이 1개인 모델에 시그모이드를 적용하므로 점수가 0~1이라 기준값과 바로 비교한다.
    """

    def __init__(
        self,
        model_name: str = RERANKER_MODEL,
        max_length: int = 512,
        batch_size: int = 16,
    ):
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()  # Gradio는 요청을 여러 스레드에서 처리한다

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        scores = self._load().predict(
            [(query, text) for text in texts],
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        return [float(score) for score in scores]

    def _load(self):
        with self._lock:
            if self._model is None:
                # torch를 함께 불러와 수 초 걸리므로 리랭커를 실제로 쓸 때 import한다
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(self.model_name, max_length=self.max_length)
        return self._model


class Retriever:
    """BM25 · 벡터 검색 → RRF 결합 → 리랭킹 → 상위 5개 (WBS 3.4.3~3.4.5)."""

    def __init__(
        self,
        items: list[dict],
        collection: Any,
        embed: Callable[[list[str]], list[list[float]]],
        reranker: Reranker | None = None,
        threshold: float = 0.55,
    ):
        """
        Args:
            items: BM25로 찾을 항목 [{"id", "text", "metadata"}] (Chroma에 넣은 것과 같음)
            collection: Chroma 컬렉션 (query만 씀)
            embed: 문장 목록 → 벡터 목록 (LLMClient.embed)
            reranker: None이면 리랭킹을 건너뛰고 RRF 순서를 그대로 쓴다
            threshold: 근거 점수 기준값 (settings.not_found_threshold)
        """
        self._items = items
        self._bm25 = (
            BM25Okapi([tokenize(item["text"]) for item in items]) if items else None
        )
        self._collection = collection
        self._embed = embed
        self._reranker = reranker
        self.threshold = threshold
        if reranker is None:
            logger.warning(
                "리랭커 없이 검색합니다. 점수가 RRF 값이라 기준값 %.2f를 다시 정해야 합니다",
                threshold,
            )

    @classmethod
    def from_settings(cls, settings: Settings, llm: LLMClient) -> Retriever:
        """앱 시작 시 1번: 색인을 열고 리랭커를 준비한다."""
        try:
            items = load_bm25_items(settings.bm25_path)
            client = chromadb.PersistentClient(path=str(settings.chroma_dir))
            collection = client.get_collection(COLLECTION_NAME)
        except Exception as error:
            raise RuntimeError(
                f"검색 색인을 열 수 없습니다 ({settings.chroma_dir}, {settings.bm25_path}). "
                "python build_index.py를 먼저 실행하세요"
            ) from error
        return cls(
            items=items,
            collection=collection,
            embed=llm.embed,
            reranker=BgeReranker(),
            threshold=settings.not_found_threshold,
        )

    def search(self, queries: list[str], equipment: dict | None, qtype: str):
        """
        검색 진입점: BM25·벡터 검색(설비 필터 적용) → RRF 결합 → 리랭킹 → 상위 5개 반환. 필터를 건 결과가 기준값에 못 미치면 필터 없이
        다시 검색하고 "다른 설비 매뉴얼 기준"으로 표시

        Returns: list[Hit], bool (필터를 풀고 다시 찾았는지 = 응답의 filter_released)
        """
        queries = [query.strip() for query in queries if query and query.strip()]
        if not queries:
            return [], False
        embeddings = self._embed(queries)  # 검색어 2~3개를 한 번에 임베딩 (API 1번)
        condition, _ = self.apply_equipment_filter(equipment, None)
        hits = self._search_once(queries, embeddings, condition, qtype)
        if condition is None:
            return hits, False
        _, should_release = self.apply_equipment_filter(equipment, hits)
        if not should_release:
            return hits, False
        unfiltered = self._search_once(queries, embeddings, None, qtype)
        if self.is_not_found(unfiltered):
            return hits, False
        logger.info("설비 필터 결과가 기준값 미달이라 필터 없이 다시 찾음: %s", equipment)
        return unfiltered, True

    def bm25_search(
        self, query: str, k: int = CANDIDATE_K, equipment: dict | None = None
    ):
        """
        BM25로 상위 20개 후보 검색 (설비 필터가 있으면 해당 설비 문서 안에서만 검색)

        Returns: list[Hit]
        """
        condition, _ = self.apply_equipment_filter(equipment, None)
        return self._bm25_ranked(query, k, condition)

    def vector_search(
        self, query: str, k: int = CANDIDATE_K, equipment: dict | None = None
    ):
        """
        벡터 유사도로 상위 20개 후보 검색 (설비 필터가 있으면 메타데이터 조건으로 해당 설비 문서만 검색)

        Returns: list[Hit]
        """
        condition, _ = self.apply_equipment_filter(equipment, None)
        return self._vector_ranked(self._embed([query])[0], k, condition)

    def rrf_merge(self, lists: list[list[Hit]]):
        """
        여러 검색 결과(검색어마다 BM25 · 벡터)를 RRF(k=60)로 합침

        Returns: list[Hit] (score는 RRF 점수. 같은 id는 처음 나온 Hit의 내용을 씀)
        """
        scores: dict[str, float] = {}
        first_seen: dict[str, Hit] = {}
        for hits in lists:
            for rank, hit in enumerate(hits, start=1):
                scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (RRF_K + rank)
                first_seen.setdefault(hit.id, hit)
        merged = [
            replace(first_seen[hit_id], score=score) for hit_id, score in scores.items()
        ]
        merged.sort(key=lambda hit: hit.score, reverse=True)
        return merged

    def rerank(self, query: str, hits: list[Hit]):
        """
        리랭커(bge-reranker-v2-m3)로 후보를 다시 정렬

        Returns: list[Hit] (score는 리랭커 점수 0~1, 원래 점수는 metadata["rrf_score"])
        """
        if self._reranker is None or not hits:
            return list(hits)
        scores = self._reranker.score(query, [hit.text for hit in hits])
        reranked = [
            replace(
                hit,
                score=float(score),
                metadata={**hit.metadata, "rrf_score": hit.score},
            )
            for hit, score in zip(hits, scores, strict=True)
        ]
        reranked.sort(key=lambda hit: hit.score, reverse=True)
        return reranked

    def apply_equipment_filter(self, equipment: dict | None, hits: list[Hit] | None):
        """
        명판으로 정한 설비를 검색 단계에서 쓸 메타데이터 조건으로 만들고, 필터를 건 결과의 리랭커 1위 점수가 기준값보다 낮으면 필터를 풀지 판단

        Returns: dict | None, bool
        """
        doc_ids = _doc_ids(equipment)
        if not doc_ids:
            return None, False
        # 모든 모델에 해당하는 KB·청크(applies_to "*")는 설비를 정해도 빼지 않는다
        condition = {"$or": [{"doc_id": {"$in": doc_ids}}, {"all_models": True}]}
        if hits is None:
            return condition, False
        return condition, self.is_not_found(hits)

    def is_not_found(self, hits: list[Hit]):
        """
        리랭커 1위 점수가 기준값(개발셋으로 결정, 예시 0.55)보다 낮으면 "확인되지 않음"으로 판단

        Returns: bool
        """
        return not hits or hits[0].score < self.threshold

    def _search_once(
        self,
        queries: list[str],
        embeddings: list[list[float]],
        condition: dict | None,
        qtype: str | None,
    ) -> list[Hit]:
        """검색어마다 BM25 · 벡터 순위를 만들어 RRF로 합치고 리랭킹한다."""
        ranked_lists = []
        for query, embedding in zip(queries, embeddings, strict=True):
            ranked_lists.append(self._bm25_ranked(query, CANDIDATE_K, condition))
            ranked_lists.append(self._vector_ranked(embedding, CANDIDATE_K, condition))
        pool = _rerank_pool(self.rrf_merge(ranked_lists), qtype, RERANK_POOL_SIZE)
        return self.rerank(" ".join(queries), pool)[:TOP_K]

    def _bm25_ranked(self, query: str, k: int, condition: dict | None) -> list[Hit]:
        tokens = tokenize(query)
        if self._bm25 is None or not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        # 점수 0은 검색어가 하나도 겹치지 않은 항목이라 후보에서 뺀다
        ranked = sorted(
            (
                i
                for i, score in enumerate(scores)
                if score > 0 and _matches(self._items[i]["metadata"], condition)
            ),
            key=lambda i: scores[i],
            reverse=True,
        )
        return [
            _to_hit(
                self._items[i]["id"],
                self._items[i]["text"],
                self._items[i]["metadata"],
                float(scores[i]),
            )
            for i in ranked[:k]
        ]

    def _vector_ranked(
        self, embedding: list[float], k: int, condition: dict | None
    ) -> list[Hit]:
        result = self._collection.query(
            query_embeddings=[embedding],
            n_results=k,
            where=condition,
            include=["documents", "metadatas", "distances"],
        )
        rows = zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
            strict=True,
        )
        # RRF는 순위만 쓰므로 거리를 "클수록 가까움"으로만 뒤집어 둔다
        return [
            _to_hit(item_id, text, metadata, 1.0 - distance)
            for item_id, text, metadata, distance in rows
        ]


def _to_hit(item_id: str, text: str, metadata: dict, score: float) -> Hit:
    return Hit(
        id=item_id,
        text=text,
        score=score,
        doc_id=metadata["doc_id"],
        page=int(metadata.get("page") or 0),
        metadata=dict(metadata),
    )


def _matches(metadata: dict, condition: dict | None) -> bool:
    """Chroma where 조건을 BM25 항목에도 똑같이 적용한다 ($or · $in · 같은 값만 지원)."""
    if condition is None:
        return True
    if "$or" in condition:
        return any(_matches(metadata, sub) for sub in condition["$or"])
    for key, expected in condition.items():
        value = metadata.get(key)
        if isinstance(expected, dict):
            if value not in expected["$in"]:
                return False
        elif value != expected:
            return False
    return True


def _is_preferred(metadata: dict, qtype: str | None) -> bool:
    """질문 유형별로 먼저 볼 자료인지 판단한다 (기획서 8.2 라우팅)."""
    chunk_type = metadata.get("chunk_type")
    if qtype in ("troubleshooting", "maintenance"):
        return chunk_type == "kb" and metadata.get("kb_type") == qtype
    if qtype == "procedure":
        return chunk_type in ("text", "table", "figure")
    return False


def _rerank_pool(merged: list[Hit], qtype: str | None, size: int) -> list[Hit]:
    """리랭커에 넣을 후보를 고른다. 질문 유형에 맞는 자료를 절반까지 먼저 넣는다.

    순서는 리랭커가 다시 정하므로, 여기서는 맞는 자료가 RRF 순위에 밀려 빠지지 않게만 한다.
    """
    preferred = [hit for hit in merged if _is_preferred(hit.metadata, qtype)][
        : size // 2
    ]
    chosen = {hit.id for hit in preferred}
    rest = [hit for hit in merged if hit.id not in chosen]
    return (preferred + rest)[:size]


def _doc_ids(equipment) -> list[str]:
    if equipment is None:
        return []
    if isinstance(equipment, dict):
        return list(equipment.get("doc_ids") or [])
    return list(getattr(equipment, "doc_ids", None) or [])
