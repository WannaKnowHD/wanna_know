"""답변 검증"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Hit


class AnswerValidator:
    """AnswerValidator (기능명세서 기준 뼈대)."""

    def validate_schema(self, answer: dict):
        """
        pydantic 모델(schemas.AnswerResponse)로 응답 JSON 형식 검사, 실패 시 1회 재요청 후 not_found

        Returns: bool
        """
        raise NotImplementedError("AnswerValidator.validate_schema")

    def verify_causes(self, answer: dict, hits: list[Hit]):
        """
        답변의 원인 id가 검색된 KB에 있는지 확인하고 없으면 제거

        Returns: dict
        """
        raise NotImplementedError("AnswerValidator.verify_causes")

    def verify_quote(self, answer: dict, hits: list[Hit]):
        """
        인용문이 원문의 부분 문자열인지 확인, 아니면 KB 원문 인용으로 교체

        Returns: dict
        """
        raise NotImplementedError("AnswerValidator.verify_quote")

    def check_language(self, answer: dict):
        """
        답변 언어가 한국어인지 확인, 아니면 재요청

        Returns: bool
        """
        raise NotImplementedError("AnswerValidator.check_language")
