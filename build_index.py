"""실행: python build_index.py → KB·색인 전체 재생성"""

from __future__ import annotations

import copy
import csv
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import tiktoken
from pydantic import TypeAdapter

from chatbot.core.schemas import Chunk, KbEntry, Section
from chatbot.rag import index_chunking, index_storage
from chatbot.rag.document_processor import KbBuilder, PdfExtractor
from chatbot.rag.glossary import Glossary
from chatbot.rag.index_cache import (
    document_cache_key,
    load_or_build_kbs,
    load_or_extract_sections,
    write_json_atomic,
)
from chatbot.rag.index_items import (
    chunk_to_item,
    format_maintenance_kb,
    format_troubleshooting_kb,
    kb_to_item,
    prepare_items,
)

if TYPE_CHECKING:
    from chatbot.core.config import Settings
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
        settings: Settings | None = None,
    ):
        self.tokenizer = tiktoken.encoding_for_model(embedding_model)
        self.root_dir = Path(__file__).resolve().parent
        self.bm25_path = Path(bm25_path) if bm25_path is not None else None
        self.chroma_dir = Path(chroma_dir) if chroma_dir is not None else None
        self.llm = llm
        self.document_names = {}
        self.document_models = {}
        self.common_doc_ids = set(common_doc_ids or ())
        self.settings = settings
        self.embedding_model = embedding_model

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

    @classmethod
    def from_settings(cls, settings: Settings) -> IndexBuilder:
        """CLI와 PDF 추가 화면에서 동일한 색인 설정을 사용한다."""
        return cls(
            embedding_model=settings.embedding_model,
            bm25_path=settings.bm25_path,
            chroma_dir=settings.chroma_dir,
            common_doc_ids={"fenner_belt"},
            settings=settings,
        )

    def _make_prefix(self, section: Section) -> str:
        """기존 호출 형식을 유지하며 청킹 모듈에 위임한다."""
        return index_chunking.make_prefix(self, section)

    def _make_chunk(
        self,
        section: Section,  # PDF에서 추출한 원래 섹션
        text: str,  # 접두부와 분할된 본문을 합친 최종 검색용 글
        section_index: int,  # 입력 목록에서 섹션의 순번
        part_index: int,  # 해당 섹션에서 만들어진 청크의 순번
    ) -> Chunk:
        """기존 호출 형식을 유지하며 청킹 모듈에 위임한다."""
        return index_chunking.make_chunk(self, section, text, section_index, part_index)

    def _count_tokens(self, text: str) -> int:
        """기존 호출 형식을 유지하며 청킹 모듈에 위임한다."""
        return index_chunking.count_tokens(self, text)

    def _chunk_atomic_section(
        self,
        section: Section,
        section_index: int,
    ) -> Chunk:
        """기존 호출 형식을 유지하며 청킹 모듈에 위임한다."""
        return index_chunking.chunk_atomic_section(self, section, section_index)

    def _split_text(self, text: str, prefix: str) -> list[str]:
        """기존 호출 형식을 유지하며 청킹 모듈에 위임한다."""
        return index_chunking.split_text(
            self,
            text,
            prefix,
            target_tokens=CHUNK_TARGET_TOKENS,
            overlap_tokens=CHUNK_OVERLAP_TOKENS,
        )

    def chunk_sections(self, sections: list[Section]) -> list[Chunk]:
        """기존 호출 형식을 유지하며 청킹 모듈에 위임한다."""
        return index_chunking.chunk_sections(
            self, sections, max_tokens=CHUNK_MAX_TOKENS
        )

    def _chunk_to_item(self, chunk: Chunk) -> dict:
        """검색 항목 변환 모듈에 위임한다."""
        return chunk_to_item(chunk)

    def _format_troubleshooting_kb(self, kb: dict) -> str:
        """검색 항목 변환 모듈에 위임한다."""
        return format_troubleshooting_kb(kb)

    def _format_maintenance_kb(self, kb: dict) -> str:
        """검색 항목 변환 모듈에 위임한다."""
        return format_maintenance_kb(kb)

    def _kb_to_item(self, kb: dict) -> dict:
        """기존 호출 형식을 유지하며 KB 변환을 위임한다."""
        return kb_to_item(
            kb,
            troubleshooting_formatter=self._format_troubleshooting_kb,
            maintenance_formatter=self._format_maintenance_kb,
        )

    def _write_json_atomic(self, path: Path, data: dict):
        """JSON 저장을 캐시 모듈에 위임한다."""
        write_json_atomic(path, data)

    def _document_cache_key(
        self,
        pdf_path: Path,
        vision_model: str,
    ) -> str:
        """기존 호출 형식을 유지하며 캐시 키 계산을 위임한다."""
        return document_cache_key(self.root_dir, pdf_path, vision_model)

    def _extract_pdf_sections(
        self,
        pdf_path: Path,
        interim_dir: Path,
    ) -> tuple[list[Section], list[Section]]:
        """KB용 원문 섹션과 검색용 복원 섹션을 구분한다."""
        extractor = PdfExtractor(
            sources_path=str(self.root_dir / "data" / "sources.csv"),
            llm=self.llm,
            interim_dir=str(interim_dir),
        )

        pages = extractor.extract_text(str(pdf_path))

        raw_sections = extractor.split_sections(pages)

        for page in pages:
            if page.regions:
                page.regions = extractor.crop_regions(
                    str(pdf_path),
                    page.page_no,
                    page.regions,
                )

        search_sections = extractor.split_sections(pages)

        return raw_sections, search_sections

    def _build_added_pdf_kbs(
        self,
        raw_sections: list[Section],
    ) -> list[dict]:
        """관리자 추가 PDF의 원문에서 KB를 생성하고 병합한다."""
        builder = KbBuilder(
            models_path=str(self.root_dir / "data" / "models.csv"),
        )

        kbs = builder.from_sections(raw_sections)

        return builder.merge_applies_to(kbs)

    def _process_pdf(
        self,
        pdf_path: Path,
        cache_root: Path,
        vision_model: str,
        *,
        is_initial: bool,
    ) -> list[dict]:
        """PDF 한 건을 캐시·청킹·KB 변환하여 검색 항목으로 만든다."""
        cache_key = self._document_cache_key(
            pdf_path,
            vision_model,
        )

        raw_sections, search_sections = load_or_extract_sections(
            pdf_path=pdf_path,
            cache_root=cache_root,
            cache_key=cache_key,
            extract_sections=self._extract_pdf_sections,
            region_cache_root=cache_root.parent,
        )

        chunks = self.chunk_sections(search_sections)

        items: list[Chunk | dict] = list(chunks)

        if not is_initial:
            kbs = load_or_build_kbs(
                raw_sections=raw_sections,
                cache_root=cache_root,
                cache_key=cache_key,
                build_kbs=self._build_added_pdf_kbs,
            )
            items.extend(kbs)

        return self._prepare_items(items)

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

    def _collect_items(
        self,
        raw_dir: Path,
        kb_dir: Path,
        cache_root: Path,
        vision_model: str,
    ) -> list[dict]:
        """전체 PDF의 검색 항목과 기존 초기 KB를 모은다."""
        raw_dir = raw_dir.resolve()
        initial_dir = raw_dir / "initial"
        admin_dir = raw_dir / "admin_add"

        pdf_paths = sorted(
            path
            for path in raw_dir.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        )

        if not pdf_paths:
            raise ValueError(f"색인할 PDF가 없습니다: {raw_dir}")

        items = []
        initial_doc_ids = set()
        seen_doc_ids = set()

        for pdf_path in pdf_paths:
            is_initial = pdf_path.is_relative_to(initial_dir)

            if not is_initial and not pdf_path.is_relative_to(admin_dir):
                raise ValueError(f"초기·관리자 폴더 밖의 PDF입니다: {pdf_path}")

            document_items = self._process_pdf(
                pdf_path,
                cache_root,
                vision_model,
                is_initial=is_initial,
            )

            doc_ids = {item["metadata"]["doc_id"] for item in document_items}

            if seen_doc_ids & doc_ids:
                raise ValueError(f"PDF 사이에 문서 ID가 중복됩니다: {pdf_path.name}")

            seen_doc_ids.update(doc_ids)

            if is_initial:
                initial_doc_ids.update(doc_ids)

            items.extend(document_items)

        if initial_doc_ids:
            initial_kbs = self._load_initial_kbs(kb_dir)

            for kb in initial_kbs:
                source_ids = {source["doc_id"] for source in kb["sources"]}
                if not source_ids <= initial_doc_ids:
                    raise ValueError(f"초기 KB의 원본 PDF가 없습니다: {kb['kb_id']}")

            items.extend(initial_kbs)

        return self._prepare_items(items)

    def _prepare_items(self, items: list[Chunk | dict]) -> list[dict]:
        """기존 호출 형식을 유지하며 검색 항목 준비를 위임한다."""
        return prepare_items(
            items,
            chunk_converter=self._chunk_to_item,
            kb_converter=self._kb_to_item,
        )

    def build_vector_index(self, items: list[Chunk | dict]):
        """기존 호출 형식을 유지하며 저장 모듈에 위임한다."""
        return index_storage.build_vector_index(self, items)

    def build_bm25_index(self, items: list[Chunk | dict]):
        """기존 호출 형식을 유지하며 저장 모듈에 위임한다."""
        return index_storage.build_bm25_index(self, items)

    def _build_indexes_at(
        self,
        items: list[Chunk | dict],
        chroma_dir: Path,
        bm25_path: Path,
    ) -> None:
        """지정한 새 위치에 두 색인을 만들고 일치 여부를 검사한다."""
        prepared = self._prepare_items(items)

        if not prepared:
            raise ValueError("색인으로 만들 검색 항목이 없습니다.")

        staged_builder = copy.copy(self)
        staged_builder.chroma_dir = chroma_dir
        staged_builder.bm25_path = bm25_path

        staged_builder.build_vector_index(prepared)
        staged_builder.build_bm25_index(prepared)

        index_storage.validate_indexes(
            chroma_dir,
            bm25_path,
        )

    def main(self):
        """
        data/raw/의 모든 PDF로 색인을 처음부터 다시 만듦.
        새 PDF만 추출·KB 변환하고 기존 문서는 캐시 재사용 → 청킹 →
        벡터·BM25 색인 생성. 새 색인은 임시 위치에 만든 뒤 완성되면 교체.
        개발자 실행(python build_index.py)과 관리자 PDF 추가에서 같이 사용.
        재생성이 이미 돌고 있으면 끝날 때까지 기다림(동시에 두 번 실행하지 않음).
        실패하면 기존 색인을 그대로 둠

        Returns: None
        챗봇이 중지된 상태에서 전체 색인을 재생성한다.
        """
        from chatbot.core.config import Settings
        from chatbot.rag.llm import LLMClient

        settings = self.settings
        if settings is None:
            settings = Settings.load()

        if self.embedding_model != settings.embedding_model:
            raise ValueError("청킹과 색인 생성의 임베딩 모델 설정이 다릅니다.")

        target_dir = settings.chroma_dir
        if target_dir.is_symlink():
            raise ValueError("색인 경로에는 심볼릭 링크를 사용하지 않습니다.")

        target_dir = target_dir.resolve()
        bm25_path = settings.bm25_path.resolve()

        if target_dir == self.root_dir or target_dir == target_dir.parent:
            raise ValueError("색인 저장 경로를 확인하세요.")

        try:
            bm25_relative = bm25_path.relative_to(target_dir)
        except ValueError as error:
            raise ValueError(
                "폴더 단위 교체를 위해 BM25 파일은 Chroma 폴더 내부에 있어야 합니다."
            ) from error

        if bm25_relative == Path("."):
            raise ValueError("BM25 경로는 파일 경로여야 합니다.")

        target_dir.parent.mkdir(parents=True, exist_ok=True)

        lock_path = target_dir.with_name(f".{target_dir.name}.rebuild.lock")
        cache_root = settings.interim_dir / "index_cache"

        with index_storage.rebuild_lock(lock_path):
            backups = list(target_dir.parent.glob(f".{target_dir.name}.backup-*"))
            if not target_dir.exists() and backups:
                raise RuntimeError(
                    "기존 경로는 없지만 색인 백업이 남아 있습니다. "
                    "이전 교체가 중단됐는지 확인하고 복구하세요."
                )

            if self.llm is None:
                self.llm = LLMClient(settings, cache_path="")

            items = self._collect_items(
                raw_dir=settings.raw_dir,
                kb_dir=settings.kb_dir,
                cache_root=cache_root,
                vision_model=settings.model_vision,
            )

            with tempfile.TemporaryDirectory(
                dir=target_dir.parent,
                prefix=f".{target_dir.name}.stage-",
            ) as temporary_dir:
                staged_dir = Path(temporary_dir)

                index_storage.run_staged_index_worker(
                    items=items,
                    settings=settings,
                    chroma_dir=staged_dir,
                    bm25_path=staged_dir / bm25_relative,
                )

                backup_dir = index_storage.replace_staged_indexes(
                    staged_dir,
                    target_dir,
                )

            print(f"색인 재생성 완료: {len(items)}개 항목")
            print(f"Chroma: {target_dir}")
            print(f"BM25: {bm25_path}")

            if backup_dir is not None:
                print(f"기존 색인 백업: {backup_dir}")


if __name__ == "__main__":
    from chatbot.core.config import Settings

    settings = Settings.load()
    builder = IndexBuilder.from_settings(settings)
    builder.main()
