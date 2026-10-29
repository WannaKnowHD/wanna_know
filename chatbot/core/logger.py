"""질의 로그 저장 (WBS 3.1.3). 설명: logger_py_분석.md"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.db.db import Database

logger = logging.getLogger(__name__)

LOG_COLUMNS = ("source", "employee_id", "question", "input_type", "question_type", "equipment_model", "kb_ids", "top_score",
               "not_found", "safety_included", "confidence", "latency_ms", "error")
JSON_COLUMNS = ("kb_ids", "latency_ms")
INSERT_LOG_SQL = f"INSERT INTO chat_logs ({', '.join(LOG_COLUMNS)}) VALUES ({', '.join(['%s'] * len(LOG_COLUMNS))})"
SOURCES = ("user", "eval")
INPUT_TYPES = {"text": "text", "voice": "text", "nameplate_photo": "nameplate", "damage_photo": "damage", "other_photo": "other"}
ERROR_MAX_CHARS = 255


class RequestLogger:
    """응답 JSON 1건을 chat_logs 한 행으로 저장한다."""

    def __init__(self, db: Database | None = None):
        self._db = db

    @staticmethod
    def build_record(answer: dict, question: str, employee_id: str | None, source: str = "user") -> dict:
        """응답 JSON → chat_logs 한 행(dict)."""
        if source not in SOURCES:
            raise ValueError(f"source는 {SOURCES} 중 하나여야 합니다: {source!r}")
        input_type = answer.get("input_type", "text")
        if input_type not in INPUT_TYPES:
            raise ValueError(f"알 수 없는 input_type: {input_type!r}")
        trace = answer.get("trace") or {}
        return {
            "source": source,
            "employee_id": employee_id,
            "question": question,
            "input_type": INPUT_TYPES[input_type],
            "question_type": answer.get("question_type"),
            "equipment_model": (answer.get("equipment") or {}).get("model"),
            "kb_ids": list(answer.get("kb_ids") or []),
            "top_score": trace.get("top_score"),
            "not_found": answer.get("status") == "not_found" and answer.get("question_type") != "out_of_scope",
            "safety_included": bool(answer.get("safety")),
            "confidence": (answer.get("image") or {}).get("confidence"),
            "latency_ms": dict(trace.get("latency_ms") or {}),
            "error": _error_text(answer),
        }

    def log_request(self, record: dict):
        """chat_logs에 INSERT. Returns: int (log_id), 실패하면 None."""
        if self._db is None:
            raise RuntimeError("RequestLogger(db)로 Database를 넘겨야 합니다")
        values = tuple(_to_db_value(column, record.get(column)) for column in LOG_COLUMNS)
        try:
            return self._db.execute(INSERT_LOG_SQL, values)
        except Exception:
            logger.exception("질의 로그 저장 실패 (source=%s)", record.get("source"))
            return None


def _error_text(answer: dict) -> str | None:
    error = (answer.get("trace") or {}).get("error")
    if error:
        text = f"{error.get('stage')}/{error.get('code')}: {error.get('message')}"
    elif answer.get("status") == "error":
        text = answer.get("message") or "error"
    else:
        return None
    return text[:ERROR_MAX_CHARS]


def _to_db_value(column: str, value):
    if column in JSON_COLUMNS and value is not None:
        return json.dumps(value, ensure_ascii=False)
    return value
