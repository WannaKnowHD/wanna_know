"""logger.py 동작 확인. MySQL 없이 sqlite 메모리 DB(tests/sqlite_db.py)로 검사함.

실행 (프로젝트 루트에서):
    pytest tests/logger_test.py -v
"""

import json
import logging
import sys
from pathlib import Path

import pytest

# 프로젝트 루트에서 chatbot 패키지 찾기
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlite_db import SqliteDb, schema_sql_columns, sqlite_columns  # noqa: E402

from chatbot.core.logger import LOG_COLUMNS, RequestLogger  # noqa: E402
from chatbot.core.schemas import AnswerResponse  # noqa: E402

# 응답 스키마 문서 3장의 전체 예시 (v0.5)
ANSWER = {
    "schema_version": "0.5",
    "status": "answered",
    "message": None,
    "input_type": "text",
    "question_type": "troubleshooting",
    "equipment": {
        "model": "138-ACC",
        "doc_ids": ["hytrol_138acc"],
        "source": "nameplate",
    },
    "filter_released": False,
    "summary": "모터 · 감속기 과열은 과부하, 모터 저전압, 감속기 윤활유 부족이 원인일 수 있습니다.",
    "safety": [
        {
            "rule_id": "S-LOTO",
            "text": "정비 전 기동 장치를 잠그세요.",
            "doc_title": None,
            "page": None,
        }
    ],
    "causes": [
        {
            "id": "TS-HYTROL138ACC-T01-04#c1",
            "cause": "과부하",
            "actions": ["적재량을 줄임"],
        },
        {
            "id": "TS-HYTROL138ACC-T01-04#c2",
            "cause": "모터 저전압",
            "actions": ["전기 담당자 점검"],
        },
    ],
    "actions": [],
    "sources": [
        {
            "ref_id": "TS-HYTROL138ACC-T01-04",
            "quote": "Motor or reducer overheating.",
            "doc_id": "hytrol_138acc",
            "doc_title": "Hytrol 138-ACC",
            "page": 22,
            "chunk_type": "kb",
            "image_path": None,
        }
    ],
    "figures": [],
    "kb_ids": ["TS-HYTROL138ACC-T01-04"],
    "image": None,
    "trace": {
        "queries_en": ["motor overheating", "reducer overheating"],
        "hits": [
            {
                "id": "TS-HYTROL138ACC-T01-04",
                "score": 0.91,
                "doc_id": "hytrol_138acc",
                "page": 22,
            }
        ],
        "top_score": 0.91,
        "threshold": 0.55,
        "latency_ms": {
            "understand": 820,
            "search": 640,
            "generate": 2100,
            "total": 3800,
        },
        "schema_retries": 0,
        "removed_cause_ids": [],
        "replaced_quote_refs": [],
        "error": None,
    },
}


def test_sample_answer_follows_response_schema():
    AnswerResponse.model_validate(ANSWER)


def test_sqlite_tables_match_schema_sql():
    db = SqliteDb()
    for table in ("chat_logs", "kb_gaps"):
        assert sqlite_columns(db, table) == schema_sql_columns(table), table
    assert set(LOG_COLUMNS) <= set(schema_sql_columns("chat_logs"))


def test_build_record_maps_answer():
    record = RequestLogger.build_record(ANSWER, "모터가 너무 뜨거워요", "20261234")
    assert record == {
        "source": "user",
        "employee_id": "20261234",
        "question": "모터가 너무 뜨거워요",
        "input_type": "text",
        "question_type": "troubleshooting",
        "equipment_model": "138-ACC",
        "kb_ids": ["TS-HYTROL138ACC-T01-04"],
        "top_score": 0.91,
        "not_found": False,
        "safety_included": True,
        "confidence": None,
        "latency_ms": {
            "understand": 820,
            "search": 640,
            "generate": 2100,
            "total": 3800,
        },
        "error": None,
    }


def test_build_record_for_photo_and_not_found():
    nameplate = {
        "status": "nameplate",
        "message": "138-ACC로 설정했습니다.",
        "input_type": "nameplate_photo",
        "image": {"kind": "nameplate", "confidence": 0.95},
    }
    record = RequestLogger.build_record(nameplate, "", "20261234")
    assert record["input_type"] == "nameplate" and record["confidence"] == 0.95
    assert record["top_score"] is None and record["kb_ids"] == []

    not_found = {
        "status": "not_found",
        "message": "확인되지 않았습니다.",
        "input_type": "text",
        "trace": {"top_score": 0.41, "threshold": 0.55},
    }
    record = RequestLogger.build_record(not_found, "쏠려요", None, source="eval")
    assert record["not_found"] is True and record["source"] == "eval"
    assert record["safety_included"] is False


def test_build_record_error_text_is_short():
    answer = {
        "status": "error",
        "message": "잠시 후 다시 시도해 주세요.",
        "input_type": "text",
        "trace": {
            "error": {"stage": "generate", "code": "llm_timeout", "message": "x" * 400}
        },
    }
    error = RequestLogger.build_record(answer, "질문", "e1")["error"]
    assert error.startswith("generate/llm_timeout: ") and len(error) == 255


def test_build_record_rejects_unknown_source():
    # ERD 주석의 'live'가 아니라 schema.sql의 'user'를 쓴다
    with pytest.raises(ValueError):
        RequestLogger.build_record(ANSWER, "질문", "e1", source="live")


def test_build_record_rejects_unknown_input_type():
    with pytest.raises(ValueError):
        RequestLogger.build_record({**ANSWER, "input_type": "photo"}, "질문", "e1")


def test_log_request_saves_row():
    db = SqliteDb()
    request_logger = RequestLogger(db)
    record = RequestLogger.build_record(ANSWER, "모터가 너무 뜨거워요", "20261234")
    log_id = request_logger.log_request(record)

    assert log_id == 1
    row = db.fetch("SELECT * FROM chat_logs WHERE log_id = %s", (log_id,))[0]
    assert row["source"] == "user" and row["input_type"] == "text"
    assert json.loads(row["kb_ids"]) == ["TS-HYTROL138ACC-T01-04"]
    assert json.loads(row["latency_ms"])["total"] == 3800
    assert row["not_found"] == 0 and row["safety_included"] == 1
    assert row["created_at"]  # DB가 채움
    assert db.fetch("SELECT * FROM kb_gaps", ()) == []  # 검토 상태는 여기서 만들지 않음


def test_log_request_returns_none_when_db_is_down(caplog):
    db = SqliteDb()
    db.broken = True
    record = RequestLogger.build_record(ANSWER, "질문", "e1")
    with caplog.at_level(logging.ERROR):
        assert RequestLogger(db).log_request(record) is None
    assert "질의 로그 저장 실패" in caplog.text


def test_log_request_needs_database():
    with pytest.raises(RuntimeError):
        RequestLogger().log_request({})


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
