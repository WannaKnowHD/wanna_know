"""용어집·설비 모델 목록 읽기"""
from __future__ import annotations

from typing import TYPE_CHECKING


class Glossary:
    """Glossary (기능명세서 기준 뼈대)."""

    def load_glossary(self, path: str):
        """
        한·영 용어집(glossary.csv)을 읽어 현장 표현 → 매뉴얼 영문 용어 사전 생성

        Returns: dict[str, list[str]]
        """
        raise NotImplementedError("Glossary.load_glossary")

    def load_models(self, path: str):
        """
        모델 목록(models.csv)을 읽어 모델명·별칭 → 문서 id 사전 생성

        Returns: dict[str, Model]
        """
        raise NotImplementedError("Glossary.load_models")
