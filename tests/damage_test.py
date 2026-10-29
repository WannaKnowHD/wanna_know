"""
vision.py DamageAnalyzer 테스트 (describe_damage · map_damage_to_kb) + 저장소의 data/damage_map.csv 확인

- 비전 모델은 가짜 LLM 클라이언트로 바꿔 끼워서 API 를 부르지 않는다
실행: python -m pytest tests/damage_test.py -v
"""
import csv
import os
import typing

import pytest

from chatbot.core.schemas import DamageResult
from chatbot.rag.glossary import LIST_SEP
from chatbot.vision.vision import DamageAnalyzer

DAMAGE_MAP = os.path.join("data", "damage_map.csv")


class FakeLLM:
    """vision() 이 정해 둔 parsed 값을 돌려주는 가짜 LLM 클라이언트"""

    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def vision(self, image, prompt, json_schema):
        self.calls.append((image, prompt, json_schema))
        return {"text": "", "parsed": self.parsed}


def analyzer(parsed=None, path=DAMAGE_MAP):
    return DamageAnalyzer(FakeLLM(parsed), path)


# ------------------------------------------------------------------------------
# describe_damage
# ------------------------------------------------------------------------------
def test_describe_damage():
    a = analyzer({"label": "edge_damage", "symptom_ko": "가장자리가 해졌어요", "confidence": 0.9})
    assert a.describe_damage(b"img") == {"label": "edge_damage", "symptom_ko": "가장자리가 해졌어요",
                                         "confidence": 0.9, "retake": False}
    image, prompt, schema = a.llm_client.calls[0]
    assert image == b"img" and "belt" in prompt and schema is DamageResult


def test_describe_damage_low_confidence_retake():
    # 확신도 0.6 미만 → 재촬영 (기능 I-03)
    result = analyzer({"label": "hole", "symptom_ko": "구멍", "confidence": 0.59}).describe_damage(b"img")
    assert result["retake"] is True


def test_describe_damage_broken_response():
    # 형식이 깨진 응답 → other · 확신도 0 · 재촬영
    assert analyzer(None).describe_damage(b"img") == {"label": "other", "symptom_ko": "", "confidence": 0.0,
                                                      "retake": True}


def test_describe_damage_empty_symptom_uses_map():
    # 증상 문장이 비면 damage_map.csv 의 대표 증상으로 (검색어가 비지 않게)
    result = analyzer({"label": "tear", "symptom_ko": " ", "confidence": 0.8}).describe_damage(b"img")
    assert result["symptom_ko"] == "벨트가 찢어졌어요"


# ------------------------------------------------------------------------------
# map_damage_to_kb
# ------------------------------------------------------------------------------
def test_map_damage_to_kb():
    a = analyzer()
    assert a.map_damage_to_kb("joint") == ["TS-FENNERBELT-T01-16", "TS-FENNERBELT-T01-17"]
    assert a.map_damage_to_kb("other") == []        # KB 후보 없음 → 증상 문장으로 일반 검색
    assert a.map_damage_to_kb("unknown") == []


def test_map_reads_bom_and_spaces(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text("﻿label,symptom_ko,kb_ids\n hole , 구멍 , A ; ;B \n", encoding="utf-8")
    a = analyzer(path=str(path))
    assert a.map_damage_to_kb("hole") == ["A", "B"]
    assert a.damage_map["hole"]["symptom_ko"] == "구멍"


# ------------------------------------------------------------------------------
# 저장소의 data/damage_map.csv
# ------------------------------------------------------------------------------
def test_repo_damage_map():
    with open(DAMAGE_MAP, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    labels = [r["label"] for r in rows]
    # 라벨은 응답 스키마 DamageResult 의 5개 값과 똑같이 (빠지거나 오타가 없게)
    assert sorted(labels) == sorted(typing.get_args(DamageResult.model_fields["label"].annotation))
    # KB id 는 kb/ 에 실제로 있는 트러블슈팅 KB 여야 한다 (KB 를 다시 만들 때 id 가 바뀌면 여기서 알 수 있음)
    for row in rows:
        for kb_id in filter(None, (k.strip() for k in row["kb_ids"].split(LIST_SEP))):
            assert os.path.exists(os.path.join("kb", "troubleshooting", f"{kb_id}.json")), kb_id
