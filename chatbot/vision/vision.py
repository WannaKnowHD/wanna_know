"""사진 판별, 명판 판독, 손상 분석"""
from __future__ import annotations

from typing import TYPE_CHECKING


class ImageRouter:
    """ImageRouter (기능명세서 기준 뼈대)."""

    def classify_image(self, image: bytes):
        """
        사진을 nameplate / belt_damage / other로 분류하고 확신도 반환. other이면 "명판이나 손상 부위를 가까이 찍어 주세요"
        안내 후 재촬영 요청

        Returns: str, float
        """
        raise NotImplementedError("ImageRouter.classify_image")


class NameplateReader:
    """NameplateReader (기능명세서 기준 뼈대)."""

    def read_nameplate(self, image: bytes):
        """
        명판에서 모델명·시리얼·제조사 판독. 판독 실패 시 재촬영 요청

        Returns: dict
        """
        raise NotImplementedError("NameplateReader.read_nameplate")

    def normalize_model(self, text: str):
        """
        대문자 변환, 공백·하이픈·점 제거 (예: "138 ACC" → 138ACC)

        Returns: str
        """
        raise NotImplementedError("NameplateReader.normalize_model")

    def match_model(self, model: str, models: dict):
        """
        models.csv와 비교: 일치 → 설비 설정 후 화면에 설비 표시 / 한 글자 차이 → 확인 질문 / 없음 → 목록에 없는 모델 안내

        Returns: Model | None, str
        """
        raise NotImplementedError("NameplateReader.match_model")


class DamageAnalyzer:
    """DamageAnalyzer (기능명세서 기준 뼈대)."""

    def describe_damage(self, image: bytes):
        """
        손상 사진을 라벨(hole / tear / joint / edge_damage / other)과 증상 문장으로 변환. 확신도 0.6 이상이면 KB
        검색·답변, 0.6 미만이면 재촬영 요청

        Returns: dict (label, symptom_ko, confidence)
        """
        raise NotImplementedError("DamageAnalyzer.describe_damage")

    def map_damage_to_kb(self, label: str):
        """
        손상 라벨 → KB 후보 매핑(damage_map.csv)

        Returns: list[str]
        """
        raise NotImplementedError("DamageAnalyzer.map_damage_to_kb")
