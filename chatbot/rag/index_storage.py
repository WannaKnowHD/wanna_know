"""기존 검색 코드와 호환되는 Chroma·BM25 색인 저장."""
from __future__ import annotations

import os
import pickle
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import chromadb

from chatbot.core.schemas import Chunk
from chatbot.rag.retriever import COLLECTION_NAME

if TYPE_CHECKING:
    from chatbot.rag.llm import LLMClient


class StorageContext(Protocol):
    """기존 저장 메서드가 사용하는 경로와 호출 계약."""

    chroma_dir: Path | None
    bm25_path: Path | None
    llm: LLMClient | None

    def _prepare_items(self, items: list[Chunk | dict]) -> list[dict]: ...


def build_vector_index(builder: StorageContext, items: list[Chunk | dict]):
    """
    KB·청크를 임베딩해 Chroma에 저장

    Returns: None

    공통 검색 항목을 임베딩해 새 Chroma 컬렉션에 저장한다.
    """
    if builder.chroma_dir is None:
        raise ValueError("Chroma 저장 경로를 지정해야 합니다.")

    if builder.llm is None:
        raise ValueError("임베딩에 사용할 LLMClient가 필요합니다.")

    prepared = builder._prepare_items(items)
    if not prepared:
        raise ValueError("빈 항목 목록으로 색인을 만들지 않습니다.")

    client = chromadb.PersistentClient(
        path=str(builder.chroma_dir),
    )

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        configuration={
            "hnsw": {
                "space": "cosine",
            },
        },
    )

    batch_size = min(32, client.get_max_batch_size())

    for start in range(0, len(prepared), batch_size):
        batch = prepared[start : start + batch_size]
        texts = [item["text"] for item in batch]

        embeddings = builder.llm.embed(texts)

        if len(embeddings) != len(batch):
            raise ValueError("임베딩 개수와 색인 항목 개수가 일치하지 않습니다.")

        collection.add(
            ids=[item["id"] for item in batch],
            documents=texts,
            metadatas=[item["metadata"] for item in batch],
            embeddings=embeddings,
        )

    if collection.count() != len(prepared):
        raise ValueError("Chroma에 저장된 항목 개수가 일치하지 않습니다.")


def build_bm25_index(builder: StorageContext, items: list[Chunk | dict]):
    """검색 담당자가 읽을 항목 목록을 pickle 파일로 저장한다."""
    if builder.bm25_path is None:
        raise ValueError("BM25 저장 경로를 지정해야 합니다.")

    prepared = builder._prepare_items(items)
    if not prepared:
        raise ValueError("빈 항목 목록으로 기존 색인을 교체하지 않습니다.")

    output_path = builder.bm25_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            pickle.dump(
                prepared,
                file,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, output_path)

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
