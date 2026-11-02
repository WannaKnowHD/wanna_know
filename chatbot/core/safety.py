"""안전 문구 붙이기 (요구사항 E-02)

팀 결정 B안: KB 는 그대로 두고, data/safety_rules.json 이 "어떤 경우에 어떤 문구"를 정한다.
KB 는 판단 재료(점검 KB 의 lockout_required, 원인·조치 글자)만 준다.

- 안전 문구는 LLM 이 만들지 않는다. 규칙 파일의 문구만 붙인다 (prompts.py 규칙 8).
- 규칙 파일은 앱 시작 때 한 번 읽고 검사한다. 파일이 없거나·비었거나·공통 규칙이 없으면 바로 오류.
- 정비·조정 답변(troubleshooting · maintenance · procedure 이면서 status = answered)에는 반드시 1개 이상 붙인다.

규칙 고르기
  1) 규칙의 doc_ids 가 답변 출처 문서와 맞고, when 조건 중 하나라도 맞으면 후보
       when 한 조건 = {question_types?, lockout_required?, keywords?, always?} (안의 항목은 모두 맞아야 함)
  2) kind 순서(KIND_ORDER)대로 최대 MAX_NOTES 개
  3) 후보가 하나도 없으면 공통 규칙(doc_ids = ["*"])
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("data") / "safety_rules.json"
ALL_DOCS = "*"
NEEDS_SAFETY = ("troubleshooting", "maintenance", "procedure")
KIND_ORDER = ("loto", "electrical", "qualified", "running", "guard", "entanglement")
MAX_NOTES = 3
REQUIRED_KEYS = ("rule_id", "kind", "doc_ids", "when", "text", "doc_title", "page")
WHEN_KEYS = {"question_types", "lockout_required", "keywords", "always"}
KB_TEXT_KEYS = ("symptom", "task", "cause", "actions", "details", "criteria", "interval_note")


class SafetyRulesError(RuntimeError):
    """safety_rules.json 이 없거나 형식이 틀림 (앱 시작 때 알림)"""


class SafetyRules:
    """SafetyRules (기능명세서 기준)

    받는 것:
      path : 규칙 파일 경로 (기본 data/safety_rules.json)
    """

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.rules = load_rules(path)
        self._common = next(rule for rule in self.rules if ALL_DOCS in rule["doc_ids"])

    def attach_safety(self, answer: dict, kb: list[dict]):
        """
        safety_rules.json 규칙으로 매뉴얼 안전 문구(LOTO, 자격자 작업, 말림 주의 등)와 출처를 붙임 (LLM이 안전 문구를 만들지
        않음)

        answer : 응답 JSON (AnswerResponse.model_dump() 모양. status · question_type · sources[].doc_id 를 읽음)
        kb     : 답변에 쓴 KB (TroubleshootingKB · MaintenanceKB 를 dict 로). 없으면 []
        Returns: dict  (answer 복사본에 safety = [{"rule_id", "text", "doc_title", "page"}, ...])
        """
        answer = dict(answer or {})
        if answer.get("status") != "answered" or answer.get("question_type") not in NEEDS_SAFETY:
            answer["safety"] = []
            return answer
        try:
            chosen = self._choose(answer, kb or [])
        except Exception as e:  # 규칙 고르다 실패해도 공통 규칙은 붙인다 (누락 0건)
            logger.exception("안전 문구 선택 실패 → 공통 규칙 사용")
            chosen = [self._common]
            trace = dict(answer.get("trace") or {})
            trace.setdefault("error", {"stage": "safety", "code": "rule_select_failed", "message": str(e)[:200]})
            answer["trace"] = trace
        answer["safety"] = [_to_note(rule) for rule in chosen]
        return answer

    def _choose(self, answer: dict, kb: list[dict]) -> list[dict]:
        facts = {
            "question_type": answer.get("question_type"),
            "lockout": _lockout(kb),
            "text": _kb_text(kb),
        }
        doc_ids = _source_doc_ids(answer)
        matched = [
            rule for rule in self.rules
            if ALL_DOCS not in rule["doc_ids"]
            and any(doc_id in rule["doc_ids"] for doc_id in doc_ids)
            and any(_condition_matches(cond, facts) for cond in rule["when"])
        ]
        if not matched:
            return [self._common]
        # kind 순서 → 출처 문서 순서 (첫 출처 문서 규칙 먼저)
        first_doc = {doc_id: i for i, doc_id in reversed(list(enumerate(doc_ids)))}
        matched.sort(key=lambda r: (KIND_ORDER.index(r["kind"]) if r["kind"] in KIND_ORDER else len(KIND_ORDER),
                                    min(first_doc.get(d, len(doc_ids)) for d in r["doc_ids"])))
        chosen, kinds = [], set()
        for rule in matched:                      # 같은 kind 는 1개만 (두 문서가 섞여도 LOTO 문구 2개 안 붙임)
            if rule["kind"] not in kinds:
                chosen.append(rule)
                kinds.add(rule["kind"])
        return chosen[:MAX_NOTES]


def load_rules(path: str | Path = DEFAULT_PATH) -> list[dict]:
    """규칙 파일을 읽고 검사한다. 문제가 있으면 SafetyRulesError."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SafetyRulesError(f"안전 규칙 파일이 없습니다: {path}") from e
    except json.JSONDecodeError as e:
        raise SafetyRulesError(f"안전 규칙 파일 형식이 틀렸습니다: {path} ({e})") from e

    rules = data.get("rules") if isinstance(data, dict) else None
    if not rules:
        raise SafetyRulesError(f"안전 규칙이 없습니다: {path} 의 rules 를 채우세요")
    seen = set()
    for i, rule in enumerate(rules):
        missing = [key for key in REQUIRED_KEYS if key not in rule]
        if missing:
            raise SafetyRulesError(f"{i}번째 규칙에 {missing} 칸이 없습니다")
        rule_id = rule["rule_id"]
        if not str(rule["text"]).strip() or not rule["doc_ids"]:
            raise SafetyRulesError(f"규칙 {rule_id} 의 text·doc_ids 가 비었습니다")
        if not rule["when"] or not all(isinstance(c, dict) and c and set(c) <= WHEN_KEYS for c in rule["when"]):
            raise SafetyRulesError(f"규칙 {rule_id} 의 when 이 틀렸습니다 (쓸 수 있는 항목: {sorted(WHEN_KEYS)})")
        if rule_id in seen:
            raise SafetyRulesError(f"규칙 id 가 겹칩니다: {rule_id}")
        seen.add(rule_id)
    if not any(ALL_DOCS in rule["doc_ids"] for rule in rules):
        raise SafetyRulesError('공통 규칙(doc_ids = ["*"])이 없습니다. 출처 문서에 규칙이 없을 때 붙일 규칙이 필요합니다')
    return rules


def _condition_matches(cond: dict, facts: dict) -> bool:
    """when 의 조건 하나 (안의 항목은 모두 맞아야 함)"""
    if cond.get("always"):
        return True
    if "question_types" in cond and facts["question_type"] not in cond["question_types"]:
        return False
    if "lockout_required" in cond and facts["lockout"] is not cond["lockout_required"]:
        return False
    if "keywords" in cond and not any(re.search(rf"\b{re.escape(k.lower())}", facts["text"]) for k in cond["keywords"]):
        return False
    return set(cond) - {"always"} != set()


def _lockout(kb: list[dict]) -> bool | None:
    """점검 KB 의 lockout_required: 하나라도 True → True, 점검 KB 가 있는데 모두 False → False, 점검 KB 없음 → None"""
    values = [bool(item.get("lockout_required")) for item in kb if item.get("type") == "maintenance"]
    if not values:
        return None
    return any(values)


def _kb_text(kb: list[dict]) -> str:
    """KB 의 증상·작업·원인·조치 글자를 소문자로 이어 붙임 (keywords 비교용)"""
    parts: list[str] = []

    def collect(value):
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for v in value:
                collect(v)
        elif isinstance(value, dict):
            for key, v in value.items():
                if key in KB_TEXT_KEYS or key == "causes":
                    collect(v)

    for item in kb:
        for key in (*KB_TEXT_KEYS, "causes"):
            collect(item.get(key))
    return " ".join(parts).lower()


def _source_doc_ids(answer: dict) -> list[str]:
    """답변 출처의 문서 id (나온 순서대로, 중복 없이)"""
    doc_ids: list[str] = []
    for source in answer.get("sources") or []:
        doc_id = source.get("doc_id") if isinstance(source, dict) else getattr(source, "doc_id", None)
        if doc_id and doc_id not in doc_ids:
            doc_ids.append(doc_id)
    return doc_ids


def _to_note(rule: dict) -> dict:
    """규칙 → schemas.SafetyNote 모양"""
    return {"rule_id": rule["rule_id"], "text": rule["text"], "doc_title": rule["doc_title"], "page": rule["page"]}