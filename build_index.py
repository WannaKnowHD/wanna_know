"""실행: python build_index.py → KB·색인 전체 재생성"""

from __future__ import annotations

import csv
import json
import os
import pickle
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import chromadb
import tiktoken
from pydantic import TypeAdapter

from chatbot.core.schemas import ALL_MODELS, Chunk, KbEntry, Section
from chatbot.rag import prompts
from chatbot.rag.glossary import Glossary
from chatbot.rag.retriever import COLLECTION_NAME

if TYPE_CHECKING:
    from chatbot.rag.llm import LLMClient

CHUNK_TARGET_TOKENS = 600
CHUNK_MAX_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 80


class IndexBuilder:
    """IndexBuilder (기능명세서 기준 뼈대)."""

    def __init__(
        self,
        embedding_model: str,
        *,
        bm25_path: str | Path | None = None,
        chroma_dir: str | Path | None = None,
        llm: LLMClient | None = None,
        common_doc_ids: set[str] | None = None,
    ):
        self.tokenizer = tiktoken.encoding_for_model(embedding_model)
        self.root_dir = Path(__file__).resolve().parent
        self.bm25_path = Path(bm25_path) if bm25_path is not None else None
        self.chroma_dir = Path(chroma_dir) if chroma_dir is not None else None
        self.llm = llm
        self.document_names = {}
        self.document_models = {}
        self.common_doc_ids = set(common_doc_ids or ())

        sources_path = self.root_dir / "data" / "sources.csv"
        with sources_path.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                self.document_names[row["doc_id"]] = row["original_file"] or row["file"]

        models_path = self.root_dir / "data" / "models.csv"
        models = Glossary().load_models(str(models_path))

        for model in models.values():
            for doc_id in model.doc_ids:
                names = self.document_models.setdefault(doc_id, [])
                if model.model_name not in names:
                    names.append(model.model_name)

    def _make_prefix(self, section: Section) -> str:
        # 청크 앞에 붙일 문서명, 연결 모델명 생성
        document_name = self.document_names.get(section.doc_id, section.doc_id)
        models = self.document_models.get(section.doc_id, [])
        if section.doc_id in self.common_doc_ids:
            model_text = prompts.ALL_MODELS_TEXT
        else:
            model_text = ", ".join(models) if models else "미지정"

        return f"Document: {document_name}\nModels: {model_text}\n\n"

    def _make_chunk(
        self,
        section: Section,  # PDF에서 추출한 원래 섹션
        text: str,  # 접두부와 분할된 본문을 합친 최종 검색용 글
        section_index: int,  # 입력 목록에서 섹션의 순번
        part_index: int,  # 해당 섹션에서 만들어진 청크의 순번
    ) -> Chunk:
        # 섹션의 출처 정보를 유지하면서 청크를 생성
        models = self.document_models.get(section.doc_id, [])
        if section.doc_id in self.common_doc_ids:
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

    def _count_tokens(self, text: str) -> int:
        # 문자열의 토큰 수를 계산
        return len(self.tokenizer.encode(text, disallowed_special=()))

    def _chunk_atomic_section(
        self,
        section: Section,
        section_index: int,
    ) -> Chunk:
        # 표, 그림을 분할하지 않고 하나의 청크로 만듦
        prefix = self._make_prefix(section)
        text = prefix + section.text

        return self._make_chunk(
            section=section,
            text=text,
            section_index=section_index,
            part_index=0,
        )

    def _split_text(self, text: str, prefix: str) -> list[str]:
        """문자 경계를 지키면서 본문을 80토큰씩 겹쳐 나눈다."""
        if not text.strip():
            return []

        prefix_tokens = self._count_tokens(prefix)
        body_limit = CHUNK_TARGET_TOKENS - prefix_tokens

        if body_limit <= CHUNK_OVERLAP_TOKENS:
            raise ValueError("문서·모델명 접두부가 너무 길어 본문을 나눌 수 없습니다.")

        tokens = self.tokenizer.encode(
            text,
            disallowed_special=(),
        )

        parts = []
        start = 0

        while start < len(tokens):
            end = min(start + body_limit, len(tokens))

            while end > start + CHUNK_OVERLAP_TOKENS or end == len(tokens):
                try:
                    part = self.tokenizer.decode(
                        tokens[start:end],
                        errors="strict",
                    )

                    if end < len(tokens):
                        self.tokenizer.decode(
                            tokens[end - CHUNK_OVERLAP_TOKENS : end],
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

            start = end - CHUNK_OVERLAP_TOKENS

        return parts

    def chunk_sections(self, sections: list[Section]) -> list[Chunk]:
        """섹션을 본문·표·그림 종류에 맞게 청킹한다."""
        chunks = []

        for section_index, section in enumerate(sections):
            if section.type not in {"text", "table", "figure"}:
                raise ValueError(f"지원하지 않는 섹션 종류입니다: {section.type}")

            if section.type in {"table", "figure"}:
                chunk = self._chunk_atomic_section(
                    section,
                    section_index,
                )
                chunks.append(chunk)
                continue

            prefix = self._make_prefix(section)
            parts = self._split_text(section.text, prefix)

            for part_index, part in enumerate(parts):
                text = prefix + part

                if self._count_tokens(text) > CHUNK_MAX_TOKENS:
                    raise ValueError(
                        f"본문 청크가 최대 토큰 수를 초과했습니다: "
                        f"{section.doc_id}, page={section.page}"
                    )

                chunk = self._make_chunk(
                    section=section,
                    text=text,
                    section_index=section_index,
                    part_index=part_index,
                )
                chunks.append(chunk)

        return chunks

    def _chunk_to_item(self, chunk: Chunk) -> dict:
        """매뉴얼 청크를 검색 코드가 읽는 공통 형식으로 변환한다."""
        metadata = {
            "doc_id": chunk.doc_id,
            "page": chunk.page,
            "chunk_type": chunk.chunk_type,
            "section_path": chunk.section_path,
            "safety_flag": chunk.safety_flag,
            "all_models": chunk.equipment_model == ALL_MODELS,
        }

        if chunk.equipment_model is not None:
            metadata["equipment_model"] = chunk.equipment_model

        if chunk.image_path is not None:
            metadata["image_path"] = chunk.image_path

        return {
            "id": chunk.chunk_id,
            "text": chunk.text,
            "metadata": metadata,
        }

    def _format_troubleshooting_kb(self, kb: dict) -> str:
        """기존 템플릿으로 고장 KB의 검색 본문을 만든다."""
        applies_to = kb["applies_to"]
        model_text = (
            prompts.ALL_MODELS_TEXT
            if applies_to == [ALL_MODELS]
            else ", ".join(applies_to)
        )

        alarm_codes = ""
        if kb.get("alarm_codes"):
            alarm_codes = prompts.KB_ALARM_CODES.format(
                codes=", ".join(kb["alarm_codes"]),
            )

        causes = []
        for cause in kb["causes"]:
            cause_text = prompts.KB_CAUSE.format(
                cause_id=cause["cause_id"],
                cause=cause["cause"],
                actions=" → ".join(cause["actions"]),
            )
            causes.append(cause_text)

        return prompts.KB_TROUBLESHOOTING.format(
            applies_to=model_text,
            symptom=kb["symptom"],
            alarm_codes=alarm_codes,
            causes="\n".join(causes),
        )

    def _format_maintenance_kb(self, kb: dict) -> str:
        """기존 템플릿으로 점검 KB의 검색 본문을 만든다."""
        applies_to = kb["applies_to"]
        model_text = (
            prompts.ALL_MODELS_TEXT
            if applies_to == [ALL_MODELS]
            else ", ".join(applies_to)
        )

        interval_parts = [kb["interval"]]

        if kb.get("interval_hours") is not None:
            hours = str(kb["interval_hours"])

            if kb.get("interval_hours_max") is not None:
                hours += f"-{kb['interval_hours_max']}"

            interval_parts.append(f"{hours} hours")

        if kb.get("first_interval_hours") is not None:
            interval_parts.append(f"first: {kb['first_interval_hours']} hours")

        if kb.get("interval_note"):
            interval_parts.append(kb["interval_note"])

        extra_parts = []

        if kb.get("criteria"):
            extra_parts.append(
                prompts.KB_CRITERIA.format(
                    criteria=kb["criteria"],
                )
            )

        if kb.get("details"):
            extra_parts.append(
                prompts.KB_DETAILS.format(
                    details="; ".join(kb["details"]),
                )
            )

        return prompts.KB_MAINTENANCE.format(
            applies_to=model_text,
            task=kb["task"],
            interval="; ".join(interval_parts),
            extra="".join(extra_parts),
        )

    def _kb_to_item(self, kb: dict) -> dict:
        """KB를 검색 코드가 읽는 공통 형식으로 변환한다."""
        kb_type = kb["type"]

        if kb_type == "troubleshooting":
            text = self._format_troubleshooting_kb(kb)
        elif kb_type == "maintenance":
            text = self._format_maintenance_kb(kb)
        else:
            raise ValueError(f"지원하지 않는 KB 종류입니다: {kb_type}")

        sources = kb["sources"]
        if not sources:
            raise ValueError(f"KB에 출처가 없습니다: {kb['kb_id']}")

        source = sources[0]
        if any(ref["doc_id"] != source["doc_id"] for ref in sources):
            raise ValueError(f"KB에 여러 문서의 출처가 섞여 있습니다: {kb['kb_id']}")

        metadata = {
            "doc_id": source["doc_id"],
            "page": source["page"],
            "chunk_type": "kb",
            "kb_type": kb_type,
            "all_models": kb["applies_to"] == [ALL_MODELS],
        }

        if kb_type == "maintenance":
            metadata["lockout_required"] = kb["lockout_required"]

        return {
            "id": kb["kb_id"],
            "text": text,
            "metadata": metadata,
        }

    def _load_initial_kbs(self, kb_dir: Path) -> list[dict]:
        """기존 초기 KB JSON을 읽고 공통 스키마로 검사한다."""
        paths = sorted(kb_dir.rglob("*.json"))

        if not paths:
            raise ValueError(f"초기 KB JSON을 찾을 수 없습니다: {kb_dir}")

        adapter = TypeAdapter(KbEntry)
        kbs = []
        seen_ids = set()

        for path in paths:
            with path.open(encoding="utf-8-sig") as file:
                raw = json.load(file)

            kb = adapter.validate_python(raw).model_dump()
            kb_id = kb["kb_id"]

            if kb_id in seen_ids:
                raise ValueError(f"초기 KB ID가 중복됩니다: {kb_id}")

            seen_ids.add(kb_id)
            kbs.append(kb)

        return kbs

    def _prepare_items(self, items: list[Chunk | dict]) -> list[dict]:
        """청크·KB를 변환하고 중복 ID와 필수 항목을 검사한다."""
        prepared = []
        seen_ids = set()

        for value in items:
            if isinstance(value, Chunk):
                item = self._chunk_to_item(value)
            elif isinstance(value, dict) and "kb_id" in value:
                item = self._kb_to_item(value)
            elif isinstance(value, dict):
                item = value
            else:
                raise TypeError("색인 입력은 Chunk 또는 dict여야 합니다.")

            item_id = item["id"]
            text = item["text"]
            metadata = item["metadata"]

            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError("색인 항목 ID가 비어 있거나 문자열이 아닙니다.")

            if item_id in seen_ids:
                raise ValueError(f"색인 항목 ID가 중복됩니다: {item_id}")

            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"검색 본문이 비어 있습니다: {item_id}")

            for key in ("doc_id", "page", "chunk_type", "all_models"):
                if key not in metadata:
                    raise ValueError(f"필수 메타데이터가 없습니다: {item_id}, {key}")

            if metadata["chunk_type"] == "kb" and metadata.get("kb_type") not in {
                "troubleshooting",
                "maintenance",
            }:
                raise ValueError(f"KB 종류가 잘못되었습니다: {item_id}")

            seen_ids.add(item_id)
            prepared.append(
                {
                    "id": item_id,
                    "text": text,
                    "metadata": dict(metadata),
                }
            )

        return prepared

    def build_vector_index(self, items: list[Chunk | dict]):
        """
        KB·청크를 임베딩해 Chroma에 저장

        Returns: None

        공통 검색 항목을 임베딩해 새 Chroma 컬렉션에 저장한다.
        """
        if self.chroma_dir is None:
            raise ValueError("Chroma 저장 경로를 지정해야 합니다.")

        if self.llm is None:
            raise ValueError("임베딩에 사용할 LLMClient가 필요합니다.")

        prepared = self._prepare_items(items)
        if not prepared:
            raise ValueError("빈 항목 목록으로 색인을 만들지 않습니다.")

        client = chromadb.PersistentClient(
            path=str(self.chroma_dir),
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

            embeddings = self.llm.embed(texts)

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

    def build_bm25_index(self, items: list[Chunk | dict]):
        """검색 담당자가 읽을 항목 목록을 pickle 파일로 저장한다."""
        if self.bm25_path is None:
            raise ValueError("BM25 저장 경로를 지정해야 합니다.")

        prepared = self._prepare_items(items)
        if not prepared:
            raise ValueError("빈 항목 목록으로 기존 색인을 교체하지 않습니다.")

        output_path = self.bm25_path
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
