"""
vision.py NameplateReader 테스트 (read_nameplate · normalize_model · match_model)

- 비전 모델은 가짜 LLM 클라이언트로 바꿔 끼워서 API 를 부르지 않는다
- 모델 목록은 저장소의 data/models.csv 를 그대로 쓴다
실행: python -m pytest tests/nameplate_test.py -v
"""
import os

import pytest

from chatbot.rag.glossary import Glossary
from chatbot.vision.vision import NameplateReader

MODELS = Glossary().load_models(os.path.join("data", "models.csv"))


class FakeLLM:
    """vision() 이 정해 둔 parsed 값을 돌려주는 가짜 LLM 클라이언트"""

    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def vision(self, image, prompt, json_schema):
        self.calls.append((image, prompt, json_schema))
        return {"text": "", "parsed": self.parsed}


def reader(parsed=None):
    return NameplateReader(FakeLLM(parsed))


# ------------------------------------------------------------------------------
# normalize_model
# ------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("138 ACC", "138ACC"),
    ("138-acc", "138ACC"),
    ("L.400", "L400"),
    ("138–LRSS", "138LRSS"),      # 긴 대시 (OCR)
    ("  mb-200-400-wd ", "MB200400WD"),
    ("", ""),
])
def test_normalize_model(text, expected):
    assert reader().normalize_model(text) == expected


# ------------------------------------------------------------------------------
# match_model
# ------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, model_name", [
    ("138-ACC", "138-ACC"),
    ("138 ACC", "138-ACC"),      # 명판 표기가 달라도 같은 모델
    ("138acc", "138-ACC"),
    ("ACC", "138-ACC"),          # models.csv 별칭
    ("L650", "L650"),
])
def test_match_exact(raw, model_name):
    model, match = reader().match_model(raw, MODELS)
    assert (model.model_name, match) == (model_name, "exact")


@pytest.mark.parametrize("raw, model_name", [
    ("138-AC", "138-ACC"),        # 한 글자 빠짐
    ("I38-ACC", "138-ACC"),       # 한 글자 바뀜 (1 → I)
    ("138-LRSSS", "138-LRSS"),    # 한 글자 더해짐
])
def test_match_near(raw, model_name):
    model, match = reader().match_model(raw, MODELS)
    assert (model.model_name, match) == (model_name, "near")


@pytest.mark.parametrize("raw", [
    "MB-200-400-WD",   # 목록에 없는 모델
    "L600",            # L400 · L500 둘 다 한 글자 차이 → 하나로 정할 수 없음
    "138-LRX",         # 138-LRS · 138-LRC 둘 다 한 글자 차이
    "",
])
def test_match_none(raw):
    assert reader().match_model(raw, MODELS) == (None, "none")


# ------------------------------------------------------------------------------
# read_nameplate
# ------------------------------------------------------------------------------
def test_read_nameplate():
    r = reader({"raw_text": " 138-ACC ", "serial": "978747", "maker": "Hytrol", "confidence": 0.95})
    assert r.read_nameplate(b"img") == {"raw_text": "138-ACC", "serial": "978747", "maker": "Hytrol",
                                         "confidence": 0.95, "retake": False}
    image, prompt, schema = r.llm_client.calls[0]
    assert image == b"img" and "nameplate" in prompt and schema.__name__ == "NameplateReading"


@pytest.mark.parametrize("parsed", [
    {"raw_text": "138-ACC", "serial": None, "maker": None, "confidence": 0.4},   # 확신도 낮음
    {"raw_text": None, "serial": "978747", "maker": "Hytrol", "confidence": 0.9},  # 모델명 못 읽음
    {"raw_text": "  ", "serial": None, "maker": None, "confidence": 0.9},         # 빈 글자
    None,                                                                         # 형식이 깨진 응답
])
def test_read_nameplate_retake(parsed):
    result = reader(parsed).read_nameplate(b"img")
    assert result["retake"] is True
