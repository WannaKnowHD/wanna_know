"""질문 1건 처리 파이프라인 (검색 → 생성 → 검증). 실행 파일 아님"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import Hit


class RagPipeline:
    """RagPipeline (기능명세서 기준 뼈대)."""

    def answer(self, question: str, state: dict, input_type: str):
        """
        질문 1건 처리 진입점: 질문 이해 → 하이브리드 검색 → 생성 → 근거 검증 → 안전 문구 → 응답 JSON. 근거 점수가 기준값 미만이면
        생성하지 않고 "확인되지 않음" 응답(not_found=true)을 반환. RequestLogger.log_request로 요청 기록 저장 (답 못
        찾음 여부를 함께 저장해 대시보드에서 따로 조회). 질문은 매번 독립적으로 처리 (이전 대화 맥락을 쓰지 않음)

        Returns: dict (응답 JSON)
        """
        raise NotImplementedError("RagPipeline.answer")

    def understand_question(self, question: str, state: dict):
        """
        질문 유형(troubleshooting / maintenance / procedure / out_of_scope) 분류, 설비·부품 힌트 추출

        Returns: QueryInfo
        """
        raise NotImplementedError("RagPipeline.understand_question")

    def rewrite_query(self, question: str, glossary: dict):
        """
        용어집으로 먼저 바꾸고, 남은 표현은 LLM으로 영문 검색어 2~3개 생성

        Returns: list[str]
        """
        raise NotImplementedError("RagPipeline.rewrite_query")

    def build_prompt(self, question: str, hits: list[Hit]):
        """
        검색된 KB·청크(출처 포함, 최대 4,000토큰)로 프롬프트 구성

        Returns: list[dict]
        """
        raise NotImplementedError("RagPipeline.build_prompt")

    def generate(self, messages: list[dict]):
        """
        LLM으로 정해진 JSON 형식의 답변 생성 (temperature 0). 요약·원인·조치·출처·그림·후속 질문 항목 포함. 후속 질문은
        설비·부품이 들어간 완전한 문장으로 생성

        Returns: dict (응답 JSON)
        """
        raise NotImplementedError("RagPipeline.generate")
