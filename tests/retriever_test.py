"""retriever.py 동작 확인. OpenAI · 리랭커 모델 없이 검사함.

가짜 임베딩 · 가짜 리랭커와 메모리 Chroma를 쓴다.

실행 (프로젝트 루트에서):
    pytest tests/retriever_test.py -v
"""

import logging
import pickle
import sys
import uuid
from pathlib import Path

import chromadb
import pytest

# 프로젝트 루트에서 chatbot 패키지 찾기
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.core.schemas import Hit, Model  # noqa: E402
from chatbot.rag.retriever import (  # noqa: E402
    RRF_K,
    Retriever,
    _rerank_pool,
    _to_hit,
    active_index_paths,
    load_bm25_items,
    tokenize,
)

HYTROL = {"model": "138-ACC", "doc_ids": ["hytrol_138acc"], "source": "nameplate"}
PRECO = {"model": "L", "doc_ids": ["preco_l_series"], "source": "nameplate"}


def kb(kb_id, doc_id, page, kb_type, text, all_models=False):
    metadata = {
        "doc_id": doc_id,
        "page": page,
        "chunk_type": "kb",
        "kb_type": kb_type,
        "all_models": all_models,
    }
    return {"id": kb_id, "text": text, "metadata": metadata}


def chunk(chunk_id, doc_id, page, text):
    metadata = {
        "doc_id": doc_id,
        "page": page,
        "chunk_type": "text",
        "all_models": False,
    }
    return {"id": chunk_id, "text": text, "metadata": metadata}


ITEMS = [
    kb(
        "TS-HYTROL138ACC-T01-04",
        "hytrol_138acc",
        22,
        "troubleshooting",
        "Motor or reducer overheating. Conveyor is overloaded. Low voltage to motor. 모터 과열",
    ),
    kb(
        "MT-HYTROL138ACC-T02-02",
        "hytrol_138acc",
        24,
        "maintenance",
        "Motor check temperature weekly. 모터 온도 확인",
    ),
    kb(
        "TS-FENNERBELT-T01-07",
        "fenner_belt",
        25,
        "troubleshooting",
        "Belt slips. Increase tension through screw take-up. 벨트 미끄러짐",
        all_models=True,
    ),
    kb(
        "TS-PRECOLSERIES-T01-02",
        "preco_l_series",
        57,
        "troubleshooting",
        "Belt runs to one side. Idlers not square with belt. 벨트 쏠림",
    ),
    chunk(
        "hytrol_138acc_p10_racked",
        "hytrol_138acc",
        10,
        "Racked sections. Measure diagonals and adjust the turnbuckle.",
    ),
]
VOCAB = sorted({token for item in ITEMS for token in tokenize(item["text"])})


def fake_embed(texts):
    """단어 개수 벡터. 1번 칸에 상수를 둬 영벡터(코사인 거리 계산 불가)를 막는다."""
    vectors = []
    for text in texts:
        tokens = tokenize(text)
        vector = [0.1] + [float(tokens.count(word)) for word in VOCAB]
        norm = sum(value * value for value in vector) ** 0.5
        vectors.append([value / norm for value in vector])
    return vectors


class OverlapReranker:
    """질문 단어가 문서에 몇 개 있는지로 0~1 점수를 준다."""

    def __init__(self):
        self.calls = []

    def score(self, query, texts):
        self.calls.append((query, len(texts)))
        words = set(tokenize(query))
        return [len(words & set(tokenize(text))) / len(words) for text in texts]


@pytest.fixture
def collection():
    client = chromadb.EphemeralClient()
    name = f"test_{uuid.uuid4().hex[:8]}"  # 메모리 클라이언트끼리 이름이 겹치지 않게
    col = client.create_collection(name, metadata={"hnsw:space": "cosine"})
    col.add(
        ids=[item["id"] for item in ITEMS],
        documents=[item["text"] for item in ITEMS],
        metadatas=[item["metadata"] for item in ITEMS],
        embeddings=fake_embed([item["text"] for item in ITEMS]),
    )
    yield col
    client.delete_collection(name)


@pytest.fixture
def retriever(collection):
    return Retriever(ITEMS, collection, fake_embed, OverlapReranker(), threshold=0.55)


def hit(hit_id, score=0.0, **metadata):
    return Hit(hit_id, f"text {hit_id}", score, "doc", 1, metadata)


def test_tokenize_splits_english_words_and_korean_bigrams():
    assert tokenize("138-ACC 모터가 뜨거워요") == [
        "138",
        "acc",
        "모터",
        "터가",
        "뜨거",
        "거워",
        "워요",
    ]
    assert tokenize("벨트") == ["벨트"]


def test_rrf_merge_adds_reciprocal_ranks():
    a, b, c = hit("A"), hit("B"), hit("C")
    merged = Retriever([], None, fake_embed, OverlapReranker()).rrf_merge(
        [[a, b], [b, c]]
    )
    assert [h.id for h in merged] == ["B", "A", "C"]
    assert merged[0].score == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert merged[1].score == pytest.approx(1 / (RRF_K + 1))


def test_bm25_search_finds_matching_kb(retriever):
    hits = retriever.bm25_search("motor overheating", 20, None)
    assert hits[0].id == "TS-HYTROL138ACC-T01-04"
    assert all(h.score > 0 for h in hits)  # 겹치는 단어가 없는 항목은 후보에서 뺀다


def test_equipment_filter_keeps_all_model_items(retriever):
    for search in (retriever.bm25_search, retriever.vector_search):
        ids = {h.id for h in search("belt", 20, PRECO)}
        assert ids <= {"TS-PRECOLSERIES-T01-02", "TS-FENNERBELT-T01-07"}, (
            search.__name__
        )
        assert "TS-FENNERBELT-T01-07" in ids  # applies_to "*"인 벨트 KB는 남는다


def test_search_returns_reranked_top5(retriever):
    hits, released = retriever.search(
        ["motor overheating", "reducer overheating"], None, "troubleshooting"
    )
    assert not released
    assert len(hits) <= 5 and hits[0].id == "TS-HYTROL138ACC-T01-04"
    assert 0 <= hits[0].score <= 1 and "rrf_score" in hits[0].metadata
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_search_embeds_all_queries_once(collection):
    calls = []

    def counting_embed(texts):
        calls.append(list(texts))
        return fake_embed(texts)

    Retriever(ITEMS, collection, counting_embed, OverlapReranker()).search(
        ["motor overheating", "low voltage"], None, "troubleshooting"
    )
    assert calls == [["motor overheating", "low voltage"]]


def test_search_releases_filter_when_top_score_is_low(retriever):
    hits, released = retriever.search(["motor overheating"], PRECO, "troubleshooting")
    assert released  # PRECO 문서에는 모터 과열이 없어 필터를 풀고 다시 찾음
    assert hits[0].id == "TS-HYTROL138ACC-T01-04"


def test_search_keeps_filter_when_score_is_enough(retriever):
    hits, released = retriever.search(["motor overheating"], HYTROL, "troubleshooting")
    assert not released
    assert {h.metadata["doc_id"] for h in hits} <= {"hytrol_138acc", "fenner_belt"}


def test_search_with_empty_queries(retriever):
    assert retriever.search([], None, "troubleshooting") == ([], False)
    assert retriever.search(["  "], HYTROL, "troubleshooting") == ([], False)


def test_apply_equipment_filter(retriever):
    assert retriever.apply_equipment_filter(None, None) == (None, False)
    assert retriever.apply_equipment_filter({"model": "X", "doc_ids": []}, None) == (
        None,
        False,
    )
    condition, release = retriever.apply_equipment_filter(HYTROL, [hit("A", 0.3)])
    assert condition == {
        "$or": [{"doc_id": {"$in": ["hytrol_138acc"]}}, {"all_models": True}]
    }
    assert release


def test_is_not_found_uses_threshold(retriever):
    assert retriever.is_not_found([])
    assert retriever.is_not_found([hit("A", 0.54)])
    assert not retriever.is_not_found([hit("A", 0.55)])


def test_rerank_pool_keeps_items_for_question_type():
    merged = [hit(f"S{i}", chunk_type="text") for i in range(6)]
    merged += [hit("KB1", chunk_type="kb", kb_type="troubleshooting")]
    pool = _rerank_pool(merged, "troubleshooting", 4)
    assert pool[0].id == "KB1" and len(pool) == 4  # RRF 7위라도 후보에 들어간다
    assert [h.id for h in _rerank_pool(merged, "out_of_scope", 4)] == [
        "S0",
        "S1",
        "S2",
        "S3",
    ]
    # 절차 질문은 매뉴얼 본문 청크(Section.type "text")를 먼저 넣는다
    kbs = [hit(f"K{i}", chunk_type="kb", kb_type="maintenance") for i in range(6)]
    pool = _rerank_pool([*kbs, hit("T1", chunk_type="text")], "procedure", 4)
    assert pool[0].id == "T1"


def test_search_without_reranker_keeps_rrf_scores(collection, caplog):
    with caplog.at_level(logging.WARNING):
        plain = Retriever(ITEMS, collection, fake_embed, None)
    assert "리랭커 없이" in caplog.text
    hits, _ = plain.search(["motor overheating"], None, "troubleshooting")
    assert hits[0].score < 0.1 and "rrf_score" not in hits[0].metadata


def test_load_bm25_items(tmp_path):
    path = tmp_path / "bm25.pkl"
    path.write_bytes(pickle.dumps(ITEMS))
    assert load_bm25_items(path) == ITEMS


def test_search_accepts_model_object(retriever):
    model = Model("138-ACC", ["ACC"], ["hytrol_138acc"])
    hits, released = retriever.search(["motor overheating"], model, "troubleshooting")
    assert not released and hits[0].id == "TS-HYTROL138ACC-T01-04"


def test_search_keeps_filter_when_nothing_found_anywhere(retriever):
    hits, released = retriever.search(["conveyor price"], PRECO, "troubleshooting")
    assert not released and retriever.is_not_found(hits)
    assert {h.metadata["doc_id"] for h in hits} <= {"preco_l_series", "fenner_belt"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_warm_up_loads_reranker_once(collection):
    reranker = OverlapReranker()
    Retriever(ITEMS, collection, fake_embed, reranker).warm_up()
    assert reranker.calls == [("warm up", 1)]   # 모델을 한 번 불러오도록 짧은 문장으로 1번 호출
    Retriever(ITEMS, collection, fake_embed, None).warm_up()   # 리랭커가 없으면 그냥 지나감


def test_rerank_skips_cached_hits(collection):
    reranker = OverlapReranker()
    retriever = Retriever(ITEMS, collection, fake_embed, reranker)
    hits = [_to_hit(item["id"], item["text"], item["metadata"], 0.0) for item in ITEMS[:3]]
    cache = {}
    first = retriever.rerank("벨트 사행", hits, cache)
    second = retriever.rerank("벨트 사행", hits, cache)   # 모두 캐시에 있으니 리랭커를 다시 부르지 않음
    assert reranker.calls == [("벨트 사행", 3)]
    assert [h.id for h in first] == [h.id for h in second]
    assert [h.score for h in first] == [h.score for h in second]


def test_active_index_paths_without_manifest_uses_settings(tmp_path):
    chroma, bm25 = tmp_path / "chroma_db", tmp_path / "chroma_db" / "bm25.pkl"
    assert active_index_paths(chroma, bm25) == (chroma, bm25)   # 예전 방식 그대로 (하위 호환)


def test_active_index_paths_reads_manifest(tmp_path):
    version = tmp_path / "chroma_db_versions" / "abc"
    version.mkdir(parents=True)
    (version / "bm25.pkl").write_bytes(b"")
    (tmp_path / "chroma_db_current.json").write_text(
        '{"schema_version": 1, "chroma_dir": "chroma_db_versions/abc", "bm25_path": "chroma_db_versions/abc/bm25.pkl"}',
        encoding="utf-8")
    chroma, bm25 = active_index_paths(tmp_path / "chroma_db", tmp_path / "chroma_db" / "bm25.pkl")
    assert chroma.resolve() == version.resolve()
    assert bm25.resolve() == (version / "bm25.pkl").resolve()


def test_active_index_paths_missing_version_raises(tmp_path):
    (tmp_path / "chroma_db_current.json").write_text(
        '{"chroma_dir": "chroma_db_versions/gone", "bm25_path": "chroma_db_versions/gone/bm25.pkl"}', encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        active_index_paths(tmp_path / "chroma_db", tmp_path / "chroma_db" / "bm25.pkl")
