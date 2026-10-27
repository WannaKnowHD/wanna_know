"""용어집·설비 모델 목록 읽기"""
from __future__ import annotations

import csv

from chatbot.core.schemas import ALL_MODELS, Model

# csv 한 칸에 여러 값을 적을 때 쓰는 구분자 (예: aliases "138 ACC;138ACC", term_en "Belt slips;Slippage")
LIST_SEP = ";"


def _split(cell: str | None) -> list[str]:
    """"a; b;;c" → ["a", "b", "c"]. 빈 칸·공백만 있는 값은 버린다."""
    return [v.strip() for v in (cell or "").split(LIST_SEP) if v.strip()]


class Glossary:
    """Glossary (기능명세서 기준 뼈대)."""

    def load_glossary(self, path: str):
        """
        한·영 용어집(glossary.csv)을 읽어 현장 표현 → 매뉴얼 영문 용어 사전 생성

        Returns: dict[str, list[str]]
        """
        # 현장 표현(field_term)과 표준어(standard_ko)를 모두 키로 넣는다.
        #   "사행"이라고 묻든 "벨트 사행"이라고 묻든 같은 영문 용어로 바뀌게 하려는 것.
        # term_en은 매뉴얼에 실제로 적힌 표현이어야 BM25 검색에 걸린다 (일반 사전식 번역 금지).
        glossary: dict[str, list[str]] = {}
        with open(path, encoding="utf-8-sig") as f:
            for line_no, row in enumerate(csv.DictReader(f), start=2):  # 1행은 헤더
                field_term = (row.get("field_term") or "").strip()
                standard_ko = (row.get("standard_ko") or "").strip()
                terms = _split(row.get("term_en"))
                if not field_term:
                    raise ValueError(f"{path}:{line_no} field_term이 비어 있음")
                if not terms:
                    raise ValueError(f"{path}:{line_no} {field_term}의 term_en이 비어 있음")
                for key in (field_term, standard_ko):
                    if not key:
                        continue
                    # 같은 키가 여러 행에 나오면 합친다 (순서 유지, 중복 제거). 표준어 하나에 현장 표현이 여럿인 경우.
                    merged = glossary.setdefault(key, [])
                    merged += [t for t in terms if t not in merged]
        return glossary

    def load_models(self, path: str):
        """
        모델 목록(models.csv)을 읽어 모델명·별칭 → 문서 id 사전 생성

        Returns: dict[str, Model]
        """
        # 키는 모델명과 별칭을 csv에 적힌 그대로 둔다 ("138-ACC", "ACC" → 같은 Model).
        # 대문자·하이픈 정리는 NameplateReader.normalize_model 한 곳에서만 하도록, 여기서는 표기를 바꾸지 않는다.
        models: dict[str, Model] = {}
        with open(path, encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀로 저장해 BOM이 붙어도 첫 열 이름이 깨지지 않게
            for line_no, row in enumerate(csv.DictReader(f), start=2):  # 1행은 헤더
                name = (row.get("model_name") or "").strip()
                if not name:
                    raise ValueError(f"{path}:{line_no} model_name이 비어 있음")
                if name == ALL_MODELS:
                    # "*"는 KB applies_to에서 "모든 모델"을 뜻하는 예약 값이라 실제 모델 이름으로 쓸 수 없다
                    raise ValueError(f'{path}:{line_no} "{ALL_MODELS}"는 모델명으로 쓸 수 없음')
                model = Model(model_name=name, aliases=_split(row.get("aliases")), doc_ids=_split(row.get("doc_ids")))
                if not model.doc_ids:
                    raise ValueError(f"{path}:{line_no} {name}의 doc_ids가 비어 있음 (어느 매뉴얼의 모델인지 알 수 없음)")
                for key in [name, *model.aliases]:
                    # 같은 이름이 두 모델을 가리키면 명판 매칭 결과가 csv 순서에 따라 달라지므로 바로 알린다
                    if key in models and models[key] is not model:
                        raise ValueError(f"{path}:{line_no} '{key}'가 {models[key].model_name}과 겹침")
                    models[key] = model
        return models
