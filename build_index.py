"""실행: python build_index.py → KB·색인 전체 재생성"""

from __future__ import annotations

import csv
from pathlib import Path

import tiktoken

from chatbot.core.schemas import Chunk, Section
from chatbot.rag.glossary import Glossary

CHUNK_TARGET_TOKENS = 600
CHUNK_MAX_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 80


class IndexBuilder:
    """IndexBuilder (기능명세서 기준 뼈대)."""

    def __init__(self, embedding_model: str):
        self.tokenizer = tiktoken.encoding_for_model(embedding_model)
        self.root_dir = Path(__file__).resolve().parent
        self.document_names = {}
        self.document_models = {}

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
        model_text = ",".join(models) if models else "미지정"

        return f"Document: {document_name}\nModels: {model_text}\n\n"

    def _make_chunk(
        self,
        section: Section,       # PDF에서 추출한 원래 섹션
        text: str,              # 접두부와 분할된 본문을 합친 최종 검색용 글
        section_index: int,     # 입력 목록에서 섹션의 순번
        part_index: int,        # 해당 섹션에서 만들어진 청크의 순번
    ) -> Chunk:
        # 섹션의 출처 정보를 유지하면서 청크를 생성
        models = self.document_models.get(section.doc_id, [])
        equipment_model = models[0] if len(models) == 1 else None

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
        return len(
            self.tokenizer.encode(text, disallowed_special=())
        )

    def chunk_sections(self, sections: list[Section]):
        """
        섹션 청크 생성 (300~600토큰, 최대 800, 겹침 80), 문서명·모델명을 앞에 붙임, 청크 메타데이터에 chunk_type,
        image_path, page 추가. 표·그림은 쪼개지 않고 1개 청크로

        Returns: list[Chunk]
        """
        raise NotImplementedError("IndexBuilder.chunk_sections")

    def build_vector_index(self, items: list[Chunk | dict]):
        """
        KB·청크를 임베딩해 Chroma에 저장

        Returns: None
        """
        raise NotImplementedError("IndexBuilder.build_vector_index")

    def build_bm25_index(self, items: list[Chunk | dict]):
        """
        KB·청크로 BM25 색인 생성 후 파일로 저장

        Returns: None
        """
        raise NotImplementedError("IndexBuilder.build_bm25_index")

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
