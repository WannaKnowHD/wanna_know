"""안전 문구 붙이기"""
from __future__ import annotations

from typing import TYPE_CHECKING


class SafetyRules:
    """SafetyRules (기능명세서 기준 뼈대)."""

    def attach_safety(self, answer: dict, kb: list[dict]):
        """
        safety_rules.json 규칙으로 매뉴얼 안전 문구(LOTO, 자격자 작업, 말림 주의 등)와 출처를 붙임 (LLM이 안전 문구를 만들지
        않음)

        Returns: dict
        """
        raise NotImplementedError("SafetyRules.attach_safety")
