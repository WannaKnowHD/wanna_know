"""실행: python eval/run_eval.py → 평가셋 자동 채점"""
from __future__ import annotations

from typing import TYPE_CHECKING

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 프로젝트 루트에서 chatbot 패키지 찾기

from dataclasses import dataclass, field


@dataclass
class Case:
    """평가 문항 1개 (testset.jsonl 한 줄)."""
    case_id: str
    question: str
    input_type: str = "text"
    gold_kb_ids: list[str] = field(default_factory=list)
    gold_causes: list[str] = field(default_factory=list)
    gold_sources: list[dict] = field(default_factory=list)
    should_abstain: bool = False


@dataclass
class Result:
    """문항 1개 실행 결과."""
    case: Case
    response: dict
    latency_ms: float


class Evaluator:
    """Evaluator (기능명세서 기준 뼈대)."""

    def load_testset(self, path: str):
        """
        평가셋(testset.jsonl) 90문항과 개발셋 20문항을 읽음

        Returns: list[Case]
        """
        raise NotImplementedError("Evaluator.load_testset")

    def run_case(self, case: Case):
        """
        문항 1개를 rag.answer로 실행하고 응답·시간 기록

        Returns: Result
        """
        raise NotImplementedError("Evaluator.run_case")

    def score_kb(self, results: list[Result]):
        """
        Top-1 KB 정확도, 검색 Recall@5 계산

        Returns: dict
        """
        raise NotImplementedError("Evaluator.score_kb")

    def score_causes(self, results: list[Result]):
        """
        원인 재현율·정밀도 계산 (원인 id 기준)

        Returns: dict
        """
        raise NotImplementedError("Evaluator.score_causes")

    def score_sources(self, results: list[Result]):
        """
        출처(문서, 페이지) 정확도 계산

        Returns: dict
        """
        raise NotImplementedError("Evaluator.score_sources")

    def score_abstention(self, results: list[Result]):
        """
        모름 처리율, 과잉 거절률, 안전 문구 포함률 계산

        Returns: dict
        """
        raise NotImplementedError("Evaluator.score_abstention")

    def write_report(self, scores: dict, results: list[Result]):
        """
        지표 CSV와 오답 목록을 results/{버전}_{날짜}.csv로 저장 (v0~v3 버전 비교용)

        Returns: None
        """
        raise NotImplementedError("Evaluator.write_report")


if __name__ == "__main__":
    Evaluator()  # TODO: load_testset → run_case → score_* → write_report 순서로 실행
