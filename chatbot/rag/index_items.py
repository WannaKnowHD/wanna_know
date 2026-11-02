"""청크·KB를 기존 검색 코드가 읽는 항목 형식으로 변환한다."""

from __future__ import annotations

from collections.abc import Callable

from chatbot.core.schemas import ALL_MODELS, Chunk
from chatbot.rag import prompts


def chunk_to_item(chunk: Chunk) -> dict:
    """매뉴얼 청크를 검색 코드가 읽는 공통 형식으로 변환한다."""
    metadata = {
        "doc_id": chunk.doc_id,
        "page": chunk.page,
        "chunk_type": chunk.chunk_type,
        "section_path": chunk.section_path,
        "safety_flag": chunk.safety_flag,
        "all_models": chunk.equipment_model == ALL_MODELS,
    }

    if chunk.equipment_model is not None:
        metadata["equipment_model"] = chunk.equipment_model

    if chunk.image_path is not None:
        metadata["image_path"] = chunk.image_path

    return {
        "id": chunk.chunk_id,
        "text": chunk.text,
        "metadata": metadata,
    }


def format_troubleshooting_kb(kb: dict) -> str:
    """기존 템플릿으로 고장 KB의 검색 본문을 만든다."""
    applies_to = kb["applies_to"]
    model_text = (
        prompts.ALL_MODELS_TEXT if applies_to == [ALL_MODELS] else ", ".join(applies_to)
    )

    alarm_codes = ""
    if kb.get("alarm_codes"):
        alarm_codes = prompts.KB_ALARM_CODES.format(
            codes=", ".join(kb["alarm_codes"]),
        )

    causes = []
    for cause in kb["causes"]:
        cause_text = prompts.KB_CAUSE.format(
            cause_id=cause["cause_id"],
            cause=cause["cause"],
            actions=prompts.KB_ACTION_SEPARATOR.join(cause["actions"]),
        )
        causes.append(cause_text)

    return prompts.KB_TROUBLESHOOTING.format(
        applies_to=model_text,
        symptom=kb["symptom"],
        alarm_codes=alarm_codes,
        causes="\n".join(causes),
    )


def format_maintenance_kb(kb: dict) -> str:
    """기존 템플릿으로 점검 KB의 검색 본문을 만든다."""
    applies_to = kb["applies_to"]
    model_text = (
        prompts.ALL_MODELS_TEXT if applies_to == [ALL_MODELS] else ", ".join(applies_to)
    )

    interval_parts = [kb["interval"]]

    if kb.get("interval_hours") is not None:
        hours = str(kb["interval_hours"])

        if kb.get("interval_hours_max") is not None:
            hours += f"-{kb['interval_hours_max']}"

        interval_parts.append(f"{hours} hours")

    if kb.get("first_interval_hours") is not None:
        interval_parts.append(f"first: {kb['first_interval_hours']} hours")

    if kb.get("interval_note"):
        interval_parts.append(kb["interval_note"])

    extra_parts = []

    if kb.get("criteria"):
        extra_parts.append(
            prompts.KB_CRITERIA.format(
                criteria=kb["criteria"],
            )
        )

    if kb.get("details"):
        extra_parts.append(
            prompts.KB_DETAILS.format(
                details="; ".join(kb["details"]),
            )
        )

    return prompts.KB_MAINTENANCE.format(
        applies_to=model_text,
        task=kb["task"],
        interval="; ".join(interval_parts),
        extra="".join(extra_parts),
    )


def kb_to_item(
    kb: dict,
    *,
    troubleshooting_formatter: Callable[[dict], str],
    maintenance_formatter: Callable[[dict], str],
) -> dict:
    """KB를 검색 코드가 읽는 공통 형식으로 변환한다."""
    kb_type = kb["type"]

    if kb_type == "troubleshooting":
        text = troubleshooting_formatter(kb)
    elif kb_type == "maintenance":
        text = maintenance_formatter(kb)
    else:
        raise ValueError(f"지원하지 않는 KB 종류입니다: {kb_type}")

    sources = kb["sources"]
    if not sources:
        raise ValueError(f"KB에 출처가 없습니다: {kb['kb_id']}")

    source = sources[0]
    if any(ref["doc_id"] != source["doc_id"] for ref in sources):
        raise ValueError(f"KB에 여러 문서의 출처가 섞여 있습니다: {kb['kb_id']}")

    metadata = {
        "doc_id": source["doc_id"],
        "page": source["page"],
        "chunk_type": "kb",
        "kb_type": kb_type,
        "all_models": kb["applies_to"] == [ALL_MODELS],
    }

    if kb_type == "maintenance":
        metadata["lockout_required"] = kb["lockout_required"]

    return {
        "id": kb["kb_id"],
        "text": text,
        "metadata": metadata,
    }


def prepare_items(
    items: list[Chunk | dict],
    *,
    chunk_converter: Callable[[Chunk], dict],
    kb_converter: Callable[[dict], dict],
) -> list[dict]:
    """청크·KB를 변환하고 중복 ID와 필수 항목을 검사한다."""
    prepared = []
    seen_ids = set()

    for value in items:
        if isinstance(value, Chunk):
            item = chunk_converter(value)
        elif isinstance(value, dict) and "kb_id" in value:
            item = kb_converter(value)
        elif isinstance(value, dict):
            item = value
        else:
            raise TypeError("색인 입력은 Chunk 또는 dict여야 합니다.")

        item_id = item["id"]
        text = item["text"]
        metadata = item["metadata"]

        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("색인 항목 ID가 비어 있거나 문자열이 아닙니다.")

        if item_id in seen_ids:
            raise ValueError(f"색인 항목 ID가 중복됩니다: {item_id}")

        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"검색 본문이 비어 있습니다: {item_id}")

        for key in ("doc_id", "page", "chunk_type", "all_models"):
            if key not in metadata:
                raise ValueError(f"필수 메타데이터가 없습니다: {item_id}, {key}")

        if metadata["chunk_type"] == "kb" and metadata.get("kb_type") not in {
            "troubleshooting",
            "maintenance",
        }:
            raise ValueError(f"KB 종류가 잘못되었습니다: {item_id}")

        seen_ids.add(item_id)
        prepared.append(
            {
                "id": item_id,
                "text": text,
                "metadata": dict(metadata),
            }
        )

    return prepared
