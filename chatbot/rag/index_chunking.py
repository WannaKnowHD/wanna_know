"""색인용 문서 청킹. 기존 IndexBuilder의 데이터와 보조 메서드를 사용한다."""
from __future__ import annotations

from typing import Protocol

import tiktoken

from chatbot.core.schemas import ALL_MODELS, Chunk, Section
from chatbot.rag import prompts


class ChunkingContext(Protocol):
    """기존 청킹 메서드가 사용하는 속성과 호출 계약."""

    tokenizer: tiktoken.Encoding
    document_names: dict
    document_models: dict
    common_doc_ids: set[str]

    def _make_prefix(self, section: Section) -> str: ...
    def _make_chunk(
        self, section: Section, text: str, section_index: int, part_index: int
    ) -> Chunk: ...
    def _count_tokens(self, text: str) -> int: ...
    def _chunk_atomic_section(self, section: Section, section_index: int) -> Chunk: ...
    def _split_text(self, text: str, prefix: str) -> list[str]: ...


def make_prefix(builder: ChunkingContext, section: Section) -> str:
    # 청크 앞에 붙일 문서명, 연결 모델명 생성
    document_name = builder.document_names.get(section.doc_id, section.doc_id)
    models = builder.document_models.get(section.doc_id, [])
    if section.doc_id in builder.common_doc_ids:
        model_text = prompts.ALL_MODELS_TEXT
    else:
        model_text = ", ".join(models) if models else "미지정"

    return f"Document: {document_name}\nModels: {model_text}\n\n"


def make_chunk(
    builder: ChunkingContext,
    section: Section,  # PDF에서 추출한 원래 섹션
    text: str,  # 접두부와 분할된 본문을 합친 최종 검색용 글
    section_index: int,  # 입력 목록에서 섹션의 순번
    part_index: int,  # 해당 섹션에서 만들어진 청크의 순번
) -> Chunk:
    # 섹션의 출처 정보를 유지하면서 청크를 생성
    models = builder.document_models.get(section.doc_id, [])
    if section.doc_id in builder.common_doc_ids:
        equipment_model = ALL_MODELS
    elif len(models) == 1:
        equipment_model = models[0]
    else:
        equipment_model = None

    chunk_id = f"{section.doc_id}:p{section.page}:s{section_index}:c{part_index}"

    return Chunk(
        chunk_id=chunk_id,
        doc_id=section.doc_id,
        text=text,
        chunk_type=section.type,
        section_path=section.section_path,
        page=section.page,
        equipment_model=equipment_model,
        image_path=section.image_path,
    )


def count_tokens(builder: ChunkingContext, text: str) -> int:
    # 문자열의 토큰 수를 계산
    return len(builder.tokenizer.encode(text, disallowed_special=()))


def chunk_atomic_section(
    builder: ChunkingContext,
    section: Section,
    section_index: int,
) -> Chunk:
    # 표, 그림을 분할하지 않고 하나의 청크로 만듦
    prefix = builder._make_prefix(section)
    text = prefix + section.text

    return builder._make_chunk(
        section=section,
        text=text,
        section_index=section_index,
        part_index=0,
    )


def split_text(
    builder: ChunkingContext,
    text: str,
    prefix: str,
    *,
    target_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """문자 경계를 지키면서 본문을 80토큰씩 겹쳐 나눈다."""
    if not text.strip():
        return []

    prefix_tokens = builder._count_tokens(prefix)
    body_limit = target_tokens - prefix_tokens

    if body_limit <= overlap_tokens:
        raise ValueError("문서·모델명 접두부가 너무 길어 본문을 나눌 수 없습니다.")

    tokens = builder.tokenizer.encode(
        text,
        disallowed_special=(),
    )

    parts = []
    start = 0

    while start < len(tokens):
        end = min(start + body_limit, len(tokens))

        while end > start + overlap_tokens or end == len(tokens):
            try:
                part = builder.tokenizer.decode(
                    tokens[start:end],
                    errors="strict",
                )

                if end < len(tokens):
                    builder.tokenizer.decode(
                        tokens[end - overlap_tokens : end],
                        errors="strict",
                    )

                break

            except UnicodeDecodeError:
                end -= 1

        else:
            raise ValueError(
                "문자 경계와 80토큰 겹침 조건을 "
                "만족하는 분할 위치를 찾을 수 없습니다."
            )

        parts.append(part)

        if end == len(tokens):
            break

        start = end - overlap_tokens

    return parts


def chunk_sections(
    builder: ChunkingContext,
    sections: list[Section],
    *,
    max_tokens: int,
) -> list[Chunk]:
    """섹션을 본문·표·그림 종류에 맞게 청킹한다."""
    chunks = []

    for section_index, section in enumerate(sections):
        if section.type not in {"text", "table", "figure"}:
            raise ValueError(f"지원하지 않는 섹션 종류입니다: {section.type}")

        if section.type in {"table", "figure"}:
            chunk = builder._chunk_atomic_section(
                section,
                section_index,
            )
            chunks.append(chunk)
            continue

        prefix = builder._make_prefix(section)
        parts = builder._split_text(section.text, prefix)

        for part_index, part in enumerate(parts):
            text = prefix + part

            if builder._count_tokens(text) > max_tokens:
                raise ValueError(
                    f"본문 청크가 최대 토큰 수를 초과했습니다: "
                    f"{section.doc_id}, page={section.page}"
                )

            chunk = builder._make_chunk(
                section=section,
                text=text,
                section_index=section_index,
                part_index=part_index,
            )
            chunks.append(chunk)

    return chunks
