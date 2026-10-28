"""기존 검색 코드와 호환되는 Chroma·BM25 색인 저장."""

from __future__ import annotations

import multiprocessing
import os
import pickle
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import chromadb
from filelock import FileLock

from chatbot.core.schemas import Chunk
from chatbot.rag.retriever import COLLECTION_NAME, load_bm25_items

if TYPE_CHECKING:
    from chatbot.rag.llm import LLMClient
    from chatbot.core.config import Settings


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


def validate_indexes(chroma_dir: Path, bm25_path: Path) -> None:
    """두 색인의 ID·본문·메타데이터가 동일한지 검사한다."""
    items = load_bm25_items(bm25_path)

    if not items:
        raise ValueError("BM25 색인에 항목이 없습니다.")

    expected = {item["id"]: (item["text"], item["metadata"]) for item in items}

    if len(expected) != len(items):
        raise ValueError("BM25 색인에 중복 ID가 있습니다.")

    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
    )

    if collection.count() != len(items):
        raise ValueError("Chroma와 BM25의 항목 개수가 다릅니다.")

    records = collection.get(
        include=["documents", "metadatas"],
    )

    actual = {
        item_id: (text, metadata)
        for item_id, text, metadata in zip(
            records["ids"],
            records["documents"],
            records["metadatas"],
            strict=True,
        )
    }

    if actual != expected:
        raise ValueError("Chroma와 BM25의 ID·본문·메타데이터가 일치하지 않습니다.")


@contextmanager
def rebuild_lock(lock_path: Path) -> Iterator[None]:
    """다른 색인 재생성이 끝날 때까지 기다린 뒤 실행한다."""
    lock_path = lock_path.resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock = FileLock(str(lock_path), timeout=-1)

    with lock:
        yield


def _staged_index_worker(
    items: list[dict],
    settings: Settings,
    chroma_dir: Path,
    bm25_path: Path,
) -> None:
    """별도 프로세스 안에서 임시 색인을 생성·검증한다."""
    from build_index import IndexBuilder
    from chatbot.rag.llm import LLMClient

    llm = LLMClient(settings, cache_path="")

    builder = IndexBuilder(
        embedding_model=settings.embedding_model,
        chroma_dir=chroma_dir,
        bm25_path=bm25_path,
        llm=llm,
    )

    builder._build_indexes_at(
        items,
        chroma_dir,
        bm25_path,
    )


def run_staged_index_worker(
    items: list[dict],
    settings: Settings,
    chroma_dir: Path,
    bm25_path: Path,
) -> None:
    """임시 색인 프로세스가 종료될 때까지 기다리고 실패를 전달한다."""
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_staged_index_worker,
        args=(items, settings, chroma_dir, bm25_path),
    )

    process.start()

    try:
        process.join()
        exit_code = process.exitcode
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
        process.close()

    if exit_code != 0:
        raise RuntimeError(
            f"임시 색인 생성·검증에 실패했습니다. 종료 코드: {exit_code}"
        )
