"""색인 생성에 사용하는 캐시 키 계산과 JSON 파일 저장."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path, data: dict):
    """JSON을 임시 파일에 완성한 뒤 최종 경로로 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def document_cache_key(
    root_dir: Path,
    pdf_path: Path,
    vision_model: str,
) -> str:
    """문서와 추출 조건이 같을 때만 재사용할 캐시 키를 만든다."""
    pdf_hash = hashlib.sha256()

    with pdf_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            pdf_hash.update(block)

    dependencies = [
        "data/sources.csv",
        "data/models.csv",
        "chatbot/rag/document_processor.py",
        "chatbot/rag/glossary.py",
        "chatbot/core/schemas.py",
        "chatbot/rag/prompts.py",
    ]

    fingerprints = {}
    for relative_path in dependencies:
        path = root_dir / relative_path
        fingerprints[relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()

    conditions = {
        "cache_version": 1,
        "pdf_path": str(pdf_path.resolve()),
        "pdf_hash": pdf_hash.hexdigest(),
        "vision_model": vision_model,
        "dependencies": fingerprints,
    }

    encoded = json.dumps(
        conditions,
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()
