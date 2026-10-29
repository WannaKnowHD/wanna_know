"""색인 생성에 사용하는 캐시 키 계산과 JSON 파일 저장."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from pydantic import TypeAdapter

from chatbot.core.schemas import KbEntry, Section


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


def section_assets_ready(sections: list[Section]) -> bool:
    """표·그림 이미지와 정상 복원 캐시가 존재하는지 확인한다."""
    for section in sections:
        if section.type not in {"table", "figure"}:
            continue

        if not section.image_path:
            return False

        image_path = Path(section.image_path)

        if not image_path.is_file():
            return False

        if not image_path.with_suffix(".json").is_file():
            return False

    return True


def load_or_extract_sections(
    pdf_path: Path,
    cache_root: Path,
    cache_key: str,
    extract_sections: Callable[
        [Path, Path],
        tuple[list[Section], list[Section]],
    ],
    *,
    region_cache_root: Path | None = None,
) -> tuple[list[Section], list[Section]]:
    """정상 섹션 캐시를 재사용하거나 PDF를 추출한다."""
    cache_dir = cache_root.resolve() / cache_key
    cache_path = cache_dir / "sections.json"
    adapter = TypeAdapter(list[Section])

    if cache_path.is_file():
        with cache_path.open(encoding="utf-8") as file:
            cached = json.load(file)

        if cached["cache_key"] != cache_key:
            raise ValueError("섹션 캐시 키가 일치하지 않습니다.")

        raw_sections = adapter.validate_python(cached["raw_sections"])
        search_sections = adapter.validate_python(cached["search_sections"])

        if raw_sections and search_sections and section_assets_ready(search_sections):
            return raw_sections, search_sections

    regions_dir = (
        region_cache_root.resolve()
        if region_cache_root is not None
        else cache_dir / "regions"
    )

    raw_sections, search_sections = extract_sections(
        pdf_path,
        regions_dir,
    )

    if not raw_sections or not search_sections:
        raise ValueError(f"사용할 섹션을 추출하지 못했습니다: {pdf_path.name}")

    if section_assets_ready(search_sections):
        write_json_atomic(
            cache_path,
            {
                "cache_key": cache_key,
                "raw_sections": [asdict(section) for section in raw_sections],
                "search_sections": [asdict(section) for section in search_sections],
            },
        )

    return raw_sections, search_sections


def load_or_build_kbs(
    raw_sections: list[Section],
    cache_root: Path,
    cache_key: str,
    build_kbs: Callable[[list[Section]], list[dict]],
) -> list[dict]:
    """관리자 추가 PDF의 자동 생성 KB를 캐시한다."""
    cache_path = cache_root.resolve() / cache_key / "auto_kbs.json"
    adapter = TypeAdapter(list[KbEntry])
    from_cache = cache_path.is_file()

    if from_cache:
        with cache_path.open(encoding="utf-8") as file:
            cached = json.load(file)

        if cached["cache_key"] != cache_key:
            raise ValueError("자동 KB 캐시 키가 일치하지 않습니다.")

        raw_kbs = cached["kbs"]
    else:
        raw_kbs = build_kbs(raw_sections)

    validated = adapter.validate_python(raw_kbs)
    kbs = [kb.model_dump() for kb in validated]

    seen_ids = set()
    document_ids = {section.doc_id for section in raw_sections}

    for kb in kbs:
        if kb["kb_id"] in seen_ids:
            raise ValueError(f"자동 생성 KB ID가 중복됩니다: {kb['kb_id']}")
        seen_ids.add(kb["kb_id"])

        source_ids = {source["doc_id"] for source in kb["sources"]}
        if len(source_ids) != 1 or not source_ids <= document_ids:
            raise ValueError(f"자동 KB의 출처 문서가 잘못되었습니다: {kb['kb_id']}")

    if not from_cache:
        write_json_atomic(
            cache_path,
            {
                "cache_key": cache_key,
                "kbs": kbs,
            },
        )

    return kbs
