"""OpenAI 호출 (텍스트·이미지·임베딩)"""
from __future__ import annotations

from typing import TYPE_CHECKING


class LLMClient:
    """LLMClient (기능명세서 기준 뼈대)."""

    def chat(self, messages: list[dict], model: str | None, json_schema: dict | None):
        """
        OpenAI 텍스트 모델 호출. 타임아웃 30초, 실패 시 대기 시간을 늘려 최대 3회 재시도, 호출마다 토큰 수·지연 시간을 로그에 기록

        Returns: dict (응답 JSON 또는 텍스트)
        """
        raise NotImplementedError("LLMClient.chat")

    def vision(self, image: bytes, prompt: str, json_schema: dict):
        """
        OpenAI 멀티모달 모델에 이미지와 질문을 보내 결과를 받음 (명판 판독·손상 사진 설명)

        Returns: dict
        """
        raise NotImplementedError("LLMClient.vision")

    def embed(self, texts: list[str]):
        """
        문장 목록을 임베딩 벡터로 변환 (text-embedding-3-small), 같은 문장은 캐시 재사용

        Returns: list[list[float]]
        """
        raise NotImplementedError("LLMClient.embed")
