"""섹션 청킹 동작 검사. API와 DB를 호출하지 않는다."""

import pytest

from build_index import (
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    IndexBuilder,
)
from chatbot.core.schemas import Section


@pytest.fixture
def builder():
    # 테스트용 모델명: 실제 API 호출 없이 토크나이저만 선택한다.
    return IndexBuilder(embedding_model="text-embedding-3-small")


def make_section(kind="text", text="Check belt tension."):
    return Section(
        doc_id="test_manual",
        section_path="Maintenance > Belt",
        page=7,
        type=kind,
        text=text,
        image_path="test/figure.png" if kind == "figure" else None,
    )


def test_empty_input(builder):
    assert builder.chunk_sections([]) == []


def test_blank_text(builder):
    section = make_section(text=" \n ")
    assert builder.chunk_sections([section]) == []


def test_short_text_preserves_source(builder):
    section = make_section()
    chunks = builder.chunk_sections([section])

    assert len(chunks) == 1
    chunk = chunks[0]

    assert chunk.doc_id == section.doc_id
    assert chunk.page == section.page
    assert chunk.section_path == section.section_path
    assert chunk.chunk_type == "text"
    assert chunk.text.endswith(section.text)
    assert "Document: test_manual" in chunk.text


@pytest.mark.parametrize("kind", ["table", "figure"])
def test_table_and_figure_are_not_split(builder, kind):
    section = make_section(kind=kind, text=" conveyor" * 1200)
    chunks = builder.chunk_sections([section])

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_type == kind
    assert chunk.text.endswith(section.text)
    assert chunk.image_path == section.image_path


def test_long_text_has_overlap_and_no_missing_tokens(builder):
    section = make_section(text=" conveyor" * 1600)
    prefix = builder._make_prefix(section)
    chunks = builder.chunk_sections([section])

    assert len(chunks) > 1

    body_tokens = []
    for chunk in chunks:
        assert chunk.text.startswith(prefix)
        assert builder._count_tokens(chunk.text) <= CHUNK_MAX_TOKENS

        body = chunk.text[len(prefix):]
        tokens = builder.tokenizer.encode(
            body,
            disallowed_special=(),
        )
        body_tokens.append(tokens)

    reconstructed = list(body_tokens[0])

    for previous, current in zip(body_tokens, body_tokens[1:]):
        assert (
            previous[-CHUNK_OVERLAP_TOKENS:]
            == current[:CHUNK_OVERLAP_TOKENS]
        )
        reconstructed.extend(current[CHUNK_OVERLAP_TOKENS:])

    original = builder.tokenizer.encode(
        section.text,
        disallowed_special=(),
    )
    assert reconstructed == original

    ids = [chunk.chunk_id for chunk in chunks]
    assert len(ids) == len(set(ids))


def test_unknown_section_type_is_rejected(builder):
    section = make_section(kind="unknown")

    with pytest.raises(ValueError, match="지원하지 않는"):
        builder.chunk_sections([section])


@pytest.mark.parametrize(
    "sentence",
    [
        "컨베이어 벨트의 장력과 가장자리 손상을 점검하세요. ",
        "안전 점검 ⚠️ 모터 온도 80°C 이하, 벨트 속도 ≤ 2m/s. 🔧 ",
    ],
    ids=["korean", "korean-symbols-emoji"],
)
def test_unicode_text_preserves_content_and_overlap(builder, sentence):
    """긴 한글·특수문자도 복원 오류나 내용 손실 없이 청킹해야 한다."""
    section = make_section(text=sentence * 200)
    prefix = builder._make_prefix(section)

    # 예외를 예상하지 않는다. 문자 경계 처리가 부족하면 이 호출에서 실패한다.
    chunks = builder.chunk_sections([section])

    assert len(chunks) > 1
    body_tokens = []
    for chunk in chunks:
        assert chunk.text.startswith(prefix)
        assert builder._count_tokens(chunk.text) <= CHUNK_MAX_TOKENS
        assert chunk.doc_id == section.doc_id
        assert chunk.page == section.page

        body = chunk.text[len(prefix):]
        assert body
        assert "\ufffd" not in body  # 원문에 없는 대체 문자가 생기면 안 된다.
        assert body in section.text
        body_tokens.append(
            builder.tokenizer.encode(body, disallowed_special=())
        )

    reconstructed = list(body_tokens[0])
    for previous, current in zip(body_tokens, body_tokens[1:]):
        assert (
            previous[-CHUNK_OVERLAP_TOKENS:]
            == current[:CHUNK_OVERLAP_TOKENS]
        )
        reconstructed.extend(current[CHUNK_OVERLAP_TOKENS:])

    original = builder.tokenizer.encode(
        section.text, disallowed_special=()
    )
    assert reconstructed == original
