"""질문 1건 처리 파이프라인 (검색 → 생성 → 검증). 실행 파일 아님"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import tiktoken
from pydantic import TypeAdapter

from chatbot.core.schemas import (
    AnswerResponse,
    Equipment,
    KbEntry,
    LLMAnswer,
    Model,
    QueryInfo,
    TraceHit,
)
from chatbot.rag.prompts import (
    ANSWER_HUMAN,
    ANSWER_SYSTEM,
    CHUNK_TYPE_LABELS,
    CONTEXT_ITEM,
    NO_CONTEXT,
    NO_EQUIPMENT,
    NO_QUESTION_TYPE,
    QUESTION_TYPE,
    REWRITE_QUERY,
)
from chatbot.rag.validator import AnswerValidator

if TYPE_CHECKING:
    from chatbot.core.schemas import Hit

# 질문 유형 (prompts.QUESTION_TYPE · schemas.QueryInfo.qtype 주석과 같은 값. 코드 상수로는 여기에만 있어서, LLM 답에서 유형을 찾는 데 씀)
QTYPES = ("troubleshooting", "maintenance", "procedure", "out_of_scope")

# ※ 아래 네 항목 이름(유형·설비·부품·증상)은 understand_question 이 읽는 이름과 같아야 함. 한쪽만 바꾸면 항상 out_of_scope 가 됨
HINT_INSTRUCTION = """

아래 네 줄 형식으로만 답하세요. 질문에 없는 항목은 '없음'이라고 쓰세요.
유형: 위 넷 중 하나
설비: 질문에 나온 설비 모델명 (원문 그대로, 예: 138-ACC)
부품: 질문에 나온 부품 (예: 벨트, 체인, 모터)
증상: 증상 한 구절 (예: 모터 과열)"""

# 답변 생성 프롬프트의 [자료] 최대 토큰 수 (기능명세서 build_prompt). 지시문(ANSWER_SYSTEM)·질문은 따로 센다
MAX_CONTEXT_TOKENS = 4000
# 토큰을 세는 규칙. gpt-4o · gpt-4.1 계열 모델이 쓰는 규칙(o200k_base)
TOKEN_ENCODING = "o200k_base"
# KB JSON 한 개를 schemas 형식(고장 KB · 점검 KB 중 하나)으로 검사하는 도구
_KB_ADAPTER = TypeAdapter(KbEntry)
# "확인되지 않음" 안내 (요구사항 Q-03: 지어내지 않고, 설비명 입력·명판 촬영을 안내)
MSG_NOT_FOUND = "자료에서 확인되지 않습니다. 설비 모델명(예: 138-ACC)과 증상을 구체적으로 적거나, 명판 사진을 올려 주세요."
MSG_OUT_OF_SCOPE = "컨베이어 정비(고장 원인·점검 주기·조정 절차)에 관한 질문에만 답할 수 있어요."
MSG_ERROR = "답변을 만드는 중 문제가 생겼어요. 잠시 후 다시 질문해 주세요."

@lru_cache(maxsize=1)
def _encoding():
    """토큰 규칙을 한 번만 불러온다. (처음 한 번은 tiktoken 이 규칙 파일을 내려받는다)"""
    return tiktoken.get_encoding(TOKEN_ENCODING)


def _count_tokens(text: str) -> int:
    """글의 토큰 수를 센다."""
    return len(_encoding().encode(text))


def _cut_to_tokens(text: str, limit: int) -> str:
    """글을 앞에서부터 limit 토큰까지만 남긴다."""
    enc = _encoding()
    return enc.decode(enc.encode(text)[:limit])


def _kind_label(hit: Hit) -> str:
    """[자료]의 종류 이름. KB 는 kb_type(고장 KB · 점검 KB), 청크는 chunk_type(본문 · 표 · 그림)으로 고른다."""
    chunk_type = hit.metadata.get("chunk_type", "")
    key = hit.metadata.get("kb_type", "") if chunk_type == "kb" else chunk_type
    return CHUNK_TYPE_LABELS.get(key, key)

def load_kb(kb_dir) -> dict[str, dict]:
    """kb/troubleshooting · kb/maintenance 의 *.json 을 읽어 {kb_id: KB dict} 로 만든다. (앱 시작 때 1번)

    answer 가 원인 id(cause_ids)로 원인 문장·조치를 복사하고, 안전 문구 판단 재료로 넘길 때 쓴다.
    schemas 의 KB 형식(TroubleshootingKB · MaintenanceKB)으로 검사해서, 틀린 파일이 있으면 바로 오류를 낸다.
    """
    entries: dict[str, dict] = {}
    for path in sorted(Path(kb_dir).glob("*/*.json")):
        entry = _KB_ADAPTER.validate_json(path.read_text(encoding="utf-8")).model_dump()
        if entry["kb_id"] in entries:
            raise ValueError(f"KB id 가 겹칩니다: {entry['kb_id']} ({path})")
        entries[entry["kb_id"]] = entry
    return entries

def _ms(since: float) -> int:
    """since(time.perf_counter 값)부터 지금까지 걸린 시간 (밀리초)"""
    return int((time.perf_counter() - since) * 1000)


def _response_equipment(state: dict | None, info: QueryInfo) -> dict | None:
    """응답의 설비 칸 (schemas.Equipment). 명판으로 정한 설비가 먼저, 없으면 질문에 나온 설비"""
    equipment = (state or {}).get("equipment")
    if isinstance(equipment, dict) and equipment.get("model_name"):
        return Equipment(model=equipment["model_name"], doc_ids=list(equipment.get("doc_ids") or []),
                         source="nameplate").model_dump()
    if info and info.equipment_hint:
        return Equipment(model=info.equipment_hint, doc_ids=[], source="question").model_dump()
    return None

def _add_tokens(trace: dict, usage: dict | None) -> None:
    """LLM 토큰 수를 trace.tokens 에 더한다 (재요청하면 두 번 합)"""
    for key in ("input_tokens", "output_tokens"):
        if usage and usage.get(key) is not None:
            trace["tokens"][key] = trace["tokens"].get(key, 0) + int(usage[key])


def _replaced_quote_refs(before: dict, after: dict) -> list[str]:
    """검증 때 인용문이 원문 문장으로 바뀐 출처의 ref_id (지워진 출처는 넣지 않음)"""
    original = {(s["ref_id"], s["quote"]) for s in before.get("sources") or []}
    return list(dict.fromkeys(s["ref_id"] for s in after["sources"] if (s["ref_id"], s["quote"]) not in original))

def _source(source: dict, hit) -> dict:
    """검증된 출처(ref_id · quote) + 검색 결과 정보 → schemas.Source 모양"""
    return {"ref_id": source["ref_id"], "quote": source["quote"], "doc_id": hit.doc_id,
            "doc_title": hit.doc_id,          # 문서 이름 대신 doc_id 를 보여 줌 (팀 결정)
            "page": hit.page, "chunk_type": hit.metadata["chunk_type"],
            "image_path": hit.metadata.get("image_path")}

class RagPipeline:
    """RagPipeline (기능명세서 기준 뼈대)."""

    def __init__(self, llm, retriever=None, validator=None, safety=None, request_logger=None,
                 glossary=None, kb=None,log_source="user"):
        """
        받는 것:
          llm            : rag.llm.LLMClient (질문 이해·검색어 변환·답변 생성에서 chat 을 호출)
          retriever      : rag.retriever.Retriever (Retriever.from_settings 로 앱 시작 때 1번 만든 것)
          validator      : rag.validator.AnswerValidator
          safety         : core.safety.SafetyRules (data/safety_rules.json)
          request_logger : core.logger.RequestLogger (chat_logs 저장). None 이면 로그를 남기지 않음
          glossary       : 용어집 dict[str, list[str]] (Glossary.load_glossary 결과). None 이면 {}
          kb             : KB dict[kb_id, dict] (kb/*.json). 원인 문장·조치를 복사할 때 씀. None 이면 {}
        answer 를 부르지 않는 쓰임(질문 이해·프롬프트만 확인)은 llm 하나만 넣어도 된다.
        """
        self.llm = llm
        self.retriever = retriever
        self.validator = validator or AnswerValidator()
        self.safety = safety
        self.request_logger = request_logger
        self.glossary = glossary or {}
        self.kb = kb or {}
        self.log_source = log_source

    def answer(self, question: str, state: dict, input_type: str):
        """
        질문 1건 처리 진입점: 질문 이해 → 하이브리드 검색 → 생성 → 근거 검증 → 안전 문구 → 응답 JSON. 근거 점수가 기준값 미만이면
        생성하지 않고 "확인되지 않음" 응답(not_found=true)을 반환. RequestLogger.log_request로 요청 기록 저장 (답 못
        찾음 여부를 함께 저장해 대시보드에서 따로 조회). 질문은 매번 독립적으로 처리 (이전 대화 맥락을 쓰지 않음)

        Returns: dict (응답 JSON)
        """
        started = time.perf_counter()
        trace: dict = {"latency_ms": {}, "tokens": {}}
        info = None
        try:
            # 1) 질문 이해
            step = time.perf_counter()
            info = self.understand_question(question, state)
            trace["latency_ms"]["understand"] = _ms(step)
            equipment = _response_equipment(state, info)
            if info.qtype == "out_of_scope":
                return self._finish(self._not_found(input_type, info, equipment, trace, MSG_OUT_OF_SCOPE),
                                    question, state, started, kb_used=[])

            # 2) 검색어 → 검색
            step = time.perf_counter()
            queries = self.rewrite_query(question, self.glossary)
            hits, filter_released = self.retriever.search(queries, (state or {}).get("equipment"), info.qtype)
            trace["latency_ms"]["search"] = _ms(step)
            trace["queries_en"] = queries
            trace["hits"] = [TraceHit.from_hit(hit).model_dump() for hit in hits]
            trace["top_score"] = hits[0].score if hits else None
            trace["threshold"] = self.retriever.threshold
            if self.retriever.is_not_found(hits):
                return self._finish(self._not_found(input_type, info, equipment, trace, MSG_NOT_FOUND,
                                                    filter_released), question, state, started, kb_used=[])

            # 3) 프롬프트 → 생성 (형식·한국어가 틀리면 1회 재요청)
            step = time.perf_counter()
            messages = self.build_prompt(question, hits, info.qtype, equipment["model"] if equipment else None)
            parsed = self._generate_checked(messages, trace)
            trace["latency_ms"]["generate"] = _ms(step)
            if parsed is None or parsed["not_found"]:
                return self._finish(self._not_found(input_type, info, equipment, trace, MSG_NOT_FOUND,
                                                    filter_released), question, state, started, kb_used=[])

            # 4) 근거 검증: KB 에 없는 원인 id 제거, 원문에 없는 인용문 교체
            step = time.perf_counter()
            checked = self.validator.verify_quote(self.validator.verify_causes(parsed, hits), hits)
            trace["removed_cause_ids"] = [c for c in parsed["cause_ids"] if c not in checked["cause_ids"]]
            trace["replaced_quote_refs"] = _replaced_quote_refs(parsed, checked)
            trace["latency_ms"]["verify"] = _ms(step)
            if not checked["cause_ids"] and not checked["actions"]:
                return self._finish(self._not_found(input_type, info, equipment, trace, MSG_NOT_FOUND,
                                                    filter_released), question, state, started, kb_used=[])

                        # 5) 응답 조립: 원인·조치 문장은 KB 에서 그대로 복사
            assembled = self._answered(input_type, info, equipment, filter_released, checked, hits, trace)
            if assembled is None:
                return self._finish(self._not_found(input_type, info, equipment, trace, MSG_NOT_FOUND,
                                                    filter_released), question, state, started, kb_used=[])
            response, kb_used = assembled
            return self._finish(response, question, state, started, kb_used=kb_used)
            raise NotImplementedError("RagPipeline.answer: 응답 조립")
        except Exception as e:  # noqa: BLE001 — 어느 단계에서 실패해도 예외를 밖으로 내지 않고 error 응답
            return self._finish(self._error(input_type, info, trace, e), question, state, started, kb_used=[])
    def _answered(self, input_type: str, info: QueryInfo, equipment, filter_released: bool,
                  checked: dict, hits: list, trace: dict) -> tuple[dict, list[dict]] | None:
        """검증된 LLM 답 → answered 응답과 답변에 쓴 KB. 출처나 원인·조치가 하나도 없으면 None (→ 확인되지 않음)"""
        by_id = {hit.id: hit for hit in hits}
        causes = [c for c in (self._cause(cause_id) for cause_id in checked["cause_ids"]) if c]
        actions = [] if info.qtype == "troubleshooting" else checked["actions"]   # 고장 답변은 causes 로만
        sources = [_source(s, by_id[s["ref_id"]]) for s in checked["sources"]]
        if not sources or not (causes or actions):
            return None
        kb_ids = list(dict.fromkeys([s["ref_id"] for s in sources if s["chunk_type"] == "kb"]
                                    + [c["id"].split("#", 1)[0] for c in causes]))
        response = {"status": "answered", "input_type": input_type, "question_type": info.qtype,
                    "equipment": equipment, "filter_released": filter_released,
                    "summary": checked["summary"], "causes": causes, "actions": actions,
                    "sources": sources, "kb_ids": kb_ids, "trace": trace}
        return response, [self.kb[kb_id] for kb_id in kb_ids if kb_id in self.kb]

    def _cause(self, cause_id: str) -> dict | None:
        """원인 id → schemas.Cause 모양 (KB 의 원인·조치 문장 그대로). KB 에 없으면 None"""
        kb = self.kb.get(cause_id.split("#", 1)[0]) or {}
        for cause in kb.get("causes") or []:
            if cause["cause_id"] == cause_id:
                return {"id": cause_id, "cause": cause["cause"], "actions": list(cause["actions"])}
        return None
    
    def _generate_checked(self, messages: list[dict], trace: dict) -> dict | None:
        """generate → 형식·한국어 검사. 틀리면 1회 다시 요청, 또 틀리면 None (→ 확인되지 않음)"""
        for _ in range(2):
            result = self.generate(messages)
            _add_tokens(trace, result.get("usage"))
            parsed = result.get("parsed")
            if self.validator.validate_schema(parsed):
                if parsed["not_found"]:            # LLM 이 자료에 없다고 답함 → 다시 묻지 않음
                    return parsed
                if self.validator.check_language(parsed):
                    return parsed
            trace["schema_retries"] = 1
        return None

    def _not_found(self, input_type: str, info: QueryInfo, equipment, trace: dict, message: str,
                   filter_released: bool = False) -> dict:
        """확인되지 않음 응답 (지어낸 원인·수치 없이 안내만)"""
        return {"status": "not_found", "message": message, "input_type": input_type,
                "question_type": info.qtype if info else None, "equipment": equipment,
                "filter_released": filter_released, "trace": trace}

    def _error(self, input_type: str, info: QueryInfo | None, trace: dict, error: Exception) -> dict:
        """처리 중 예외 → error 응답. 원인은 trace.error 에 남긴다 (화면에는 안내 문구만)"""
        # latency_ms 에 아직 기록되지 않은 첫 단계 = 실패한 단계
        stage = next((s for s in ("understand", "search", "generate","verify") if s not in trace["latency_ms"]), "answer")
        trace["error"] = {"stage": stage, "code": type(error).__name__, "message": str(error)[:200]}
        print(f"[pipeline] answer 실패 ({stage}): {type(error).__name__}: {error}")
        return {"status": "error", "message": MSG_ERROR, "input_type": input_type,
                "question_type": info.qtype if info else None, "trace": trace}

    def _finish(self, response: dict, question: str, state: dict, started: float, kb_used: list[dict]) -> dict:
        """응답 마무리: 안전 문구 → 전체 시간 → AnswerResponse 형식 검사 → 로그 저장 → dict"""
        if self.safety is not None:
            step = time.perf_counter()
            response = self.safety.attach_safety(response, kb_used)   # 정비 답변이 아니면 safety = []
            response["trace"]["latency_ms"]["safety"] = _ms(step)
        response["trace"]["latency_ms"]["total"] = _ms(started)
        answer = AnswerResponse.model_validate(response).model_dump()
        self._log(answer, question, state)
        return answer

    def _log(self, answer: dict, question: str, state: dict) -> None:
        """chat_logs 에 1행 저장. 저장이 실패해도 답변은 그대로 돌려준다 (사원번호는 출력하지 않음)"""
        if self.request_logger is None:
            return
        user = (state or {}).get("user")
        try:
            record = self.request_logger.build_record(answer, question, getattr(user, "employee_id", None),
                                                      source=self.log_source)
            self.request_logger.log_request(record)
        except Exception as e:  # noqa: BLE001 — 로그 저장 실패로 답변을 막지 않음
            print(f"[pipeline] 로그 저장 실패: {type(e).__name__}: {e}")
            
    def understand_question(self, question: str, state: dict):
        """
        질문 유형(troubleshooting / maintenance / procedure / out_of_scope) 분류, 설비·부품 힌트 추출

        Returns: QueryInfo
        """
        # 명판으로 정해 둔 설비의 모델명 (state["equipment"] 는 schemas.Model 또는 {"model_name": ...})
        equipment = (state or {}).get("equipment")
        if isinstance(equipment, Model):
            equipment = equipment.model_name
        elif isinstance(equipment, dict):
            equipment = equipment.get("model_name")
        # 문자열일 때만 쓰고, 공백뿐이면 None  → (equipment.strip() or None) if 문자열 else None
        state_equipment = equipment.strip() or None if isinstance(equipment, str) else None

        try:
            text = self.llm.chat(QUESTION_TYPE.format(question=question) + HINT_INSTRUCTION)["text"]
        except Exception as e:  # LLM 호출 실패: 분류할 수 없으므로 아래에서 기본값으로 처리
            print(f"[pipeline] 질문 이해 실패 → 기본값 사용: {type(e).__name__}: {e}")
            text = ""

        # "유형: ...", "설비: ..." 줄을 {"유형": ..., "설비": ...} 로 읽음
        fields = {}
        for line in text.replace("：", ":").splitlines():
            name, _, value = line.partition(":")
            fields[name.strip(" *-•#`")] = value.strip(" *`")  # **유형**: ... 처럼 굵게 쓴 답도 읽음

        # 답에 유형 이름이 들어 있는지 찾음
        decision = fields.get("유형", "").lower()
        for qtype in QTYPES:                    # 답에 유형 이름이 들어 있으면 그 유형으로 정하고 멈춤
            if qtype in decision:
                break
        else:
            # 분류하지 못했으면 정비와 무관한 질문으로 보고 검색하지 않음 (확실하지 않은 질문에는 답하지 않는 쪽이 안전)
            return QueryInfo(qtype="out_of_scope", equipment_hint=state_equipment)

        # "없음" 같은 답은 힌트가 없는 것으로 봄
        equipment_hint, part_hint, symptom = [
            None if v.lower() in ("", "없음", "null", "none", "n/a", "-") else v
            for v in (fields.get("설비", ""), fields.get("부품", ""), fields.get("증상", ""))
        ]
        # 질문에 설비가 적혀 있으면 그것을, 없으면 명판으로 정해 둔 설비를 씀
        return QueryInfo(qtype=qtype, equipment_hint=equipment_hint or state_equipment, part_hint=part_hint, symptom=symptom)

    def rewrite_query(self, question: str, glossary: dict):
        """
        용어집으로 먼저 바꾸고, 남은 표현은 LLM으로 영문 검색어 2~3개 생성

        Returns: list[str]
        """
        question = (question or "").strip()
        if not question:
            return []

        # 1) 질문에 나온 현장 표현을 용어집에서 찾음 → {현장 표현: [영문 용어, ...]}
        #    공백·대소문자는 무시하고("벨트미끄러짐" = "벨트 미끄러짐"), 긴 표현부터 찾고, 이미 찾은 긴 표현 안의 짧은 표현은 건너뜀
        question_norm = "".join(question.split()).lower()
        matched: dict[str, list[str]] = {}
        for term in sorted(glossary or {}, key=len, reverse=True):
            term_norm = "".join(term.split()).lower()
            if not term_norm or term_norm not in question_norm:
                continue
            if any(term_norm in "".join(found.split()).lower() for found in matched):
                continue
            matched[term] = glossary[term]  # Glossary.load_glossary 가 영문 용어 목록(비어 있지 않음)으로 정리해 둠

        # 2) 용어집에 걸린 표현은 영문 용어를 정해서 LLM 에게 주고(REWRITE_QUERY 가 "이 용어를 우선 쓰라"고 함), 나머지만 LLM 이 바꿈
        glossary_text = "\n".join(f"{k} → {'; '.join(v)}" for k, v in matched.items()) or "(none)"
        queries: list[str] = []
        try:
            text = self.llm.chat(REWRITE_QUERY.format(glossary=glossary_text, question=question))["text"]
            # 프롬프트가 한 줄에 검색어 하나씩 쓰게 하므로 줄 단위로 읽음 ("1. ", "- " 같은 목록 기호·따옴표는 지움)
            queries = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip(" \"'`") for line in text.splitlines()]
        except Exception as e:  # LLM 실패: 용어집 결과만으로 검색을 계속함
            print(f"[pipeline] 검색어 변환 실패 → 용어집만 사용: {type(e).__name__}: {e}")
        queries = [q for q in queries if q]

        # 3) LLM 이 용어집 용어를 하나도 쓰지 않았다면, 걸린 표현마다 첫 영문 용어를 검색어로 만들어 맨 앞에 둠
        #    (용어집 용어는 매뉴얼에 실제로 적힌 표현이라 BM25 검색에 걸림)
        if matched and not any(t.lower() in q.lower() for q in queries for v in matched.values() for t in v):
            queries[:0] = [v[0] for v in matched.values()]

        # 대소문자만 다른 검색어는 하나만 남김 ("Motor Overheating" = "motor overheating")
        unique: list[str] = []
        for q in queries:
            if q.lower() not in (u.lower() for u in unique):
                unique.append(q)
        # 검색어는 2~3개(REWRITE_QUERY). 모두 실패해도 검색은 계속할 수 있게 원래 질문이라도 돌려줌
        return unique[:3] or [question]

    def build_prompt(self, question: str, hits: list[Hit], question_type: str | None = None,
                     equipment: str | None = None):
        """
        검색된 KB·청크(출처 포함, 최대 4,000토큰)로 프롬프트 구성

        받는 것:
          question      : 사용자 질문 원문
          hits          : 검색 결과 (Retriever.search 가 돌려준 순위 순서)
          question_type : understand_question 이 정한 유형 (QueryInfo.qtype). 없으면 "지정 안 됨"
          equipment     : 설비 모델명 (QueryInfo.equipment_hint). 없으면 "지정 안 됨"
        돌려주는 것: [{"role": "system", ...}, {"role": "user", ...}]  → generate 에 그대로 넘긴다
        [자료]는 검색 순위대로 붙이고, 4,000토큰을 넘기 직전에 멈춘다.
        1위 자료 하나만으로 넘으면 앞부분만 잘라서 넣는다. (자료가 빈 채로 LLM 을 부르지 않게)
        """
        items: list[str] = []
        used = 0
        separator = _count_tokens("\n\n")
        for hit in hits or []:
            item = CONTEXT_ITEM.format(ref_id=hit.id, doc_id=hit.doc_id, page=hit.page,
                                       kind=_kind_label(hit), text=hit.text)
            cost = _count_tokens(item) + (separator if items else 0)
            if used + cost > MAX_CONTEXT_TOKENS:
                if not items:
                    items.append(_cut_to_tokens(item, MAX_CONTEXT_TOKENS))
                break   # 순위를 지키기 위해, 뒤에 짧은 자료가 있어도 여기서 멈춘다
            items.append(item)
            used += cost

        human = ANSWER_HUMAN.format(
            context="\n\n".join(items) or NO_CONTEXT,
            question_type=question_type or NO_QUESTION_TYPE,
            equipment=(equipment or "").strip() or NO_EQUIPMENT,
            question=question,
        )
        return [{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": human}]

    def generate(self, messages: list[dict]):
        """
        LLM으로 정해진 JSON 형식의 답변 생성 (temperature 0). 요약·원인·조치·출처 항목 포함

        받는 것: build_prompt 가 만든 messages
        돌려주는 것: dict (LLMClient.chat 의 결과 그대로)
          parsed     : LLMAnswer 형식의 dict (not_found · summary · causes · actions · sources).
                       형식이 깨지면 None → 다시 요청할지는 AnswerValidator.validate_schema 가 정한다
          latency_ms : 걸린 시간(ms) → trace.latency_ms["generate"]
          model · text : 사용한 모델 이름, 원래 답 글자
        temperature 0 · 30초 제한 · 3번 재시도는 llm.py 에 정해져 있다.
        3번 다시 시도해도 실패하면 예외가 그대로 나간다 → answer 가 status="error" 로 처리한다.
        """
        return self.llm.chat(messages, json_schema=LLMAnswer)

if __name__ == "__main__":
    # 직접 실행해서 점검 (실제 OpenAI 를 호출함). 프로젝트 최상단 폴더에서, 가상환경을 켠 상태로:
    #```
    #   python -m chatbot.pipeline
    #```
    # .env 가 모두 채워져 있어야 한다. (python chatbot/core/config.py 가 [OK])
    from chatbot.core.config import Settings
    from chatbot.rag.llm import LLMClient

    pipeline = RagPipeline(LLMClient(Settings.load()))

    # 용어집(glossary.csv)을 읽는 코드가 아직 없어서, 시험용으로 몇 개만 직접 적음 (현장 표현 → 매뉴얼 영문 용어)
    glossary = {
        "벨트 미끄러짐": ["belt slip", "belt slippage"],
        "벨트 쏠림": ["belt tracking", "belt mistracking"],
        "과열": ["overheating"],
        "윤활": ["lubrication"],
    }
    nameplate = {"equipment": {"model_name": "138ACC"}}  # 명판으로 설비가 정해진 상태

    failures = 0

    # ---- 1) understand_question : (질문, state, 기대 유형, 기대 설비 힌트 — None 이면 확인 안 함)
    print("=" * 70 + "\n[understand_question]")
    cases = [
        ("모터가 과열되고 벨트가 미끄러져요", {}, "troubleshooting", None),
        ("138-ACC 모터에서 소음이 나요", {}, "troubleshooting", "138-ACC"),
        ("체인 윤활은 얼마마다 해야 하나요?", {}, "maintenance", None),
        ("벨트 장력 조절 방법 알려주세요", {}, "procedure", None),
        ("벨트가 한쪽으로 쏠려요", nameplate, "troubleshooting", "138ACC"),   # 질문에 설비가 없으면 명판 설비를 씀
        ("오늘 점심 뭐 먹을까요?", {}, "out_of_scope", None),
        ("롤러 가격이 얼마예요?", {}, "out_of_scope", None),
    ]
    for question, state, want_qtype, want_equipment in cases:
        info = pipeline.understand_question(question, state)
        ok = info.qtype == want_qtype and (want_equipment is None or info.equipment_hint == want_equipment)
        failures += not ok
        print(f"[{'OK' if ok else 'MISMATCH'}] {question}")
        print(f"     qtype={info.qtype} (기대 {want_qtype}) / 설비={info.equipment_hint}"
              f"{f' (기대 {want_equipment})' if want_equipment else ''} / 부품={info.part_hint} / 증상={info.symptom}")

    # ---- 2) rewrite_query : 검색어가 1~3개의 비어 있지 않은 문자열이면 통과 (내용은 눈으로 확인)
    print("=" * 70 + "\n[rewrite_query]")
    for question in ["모터가 과열되고 벨트가 미끄러져요", "벨트가 한쪽으로 쏠려요",
                     "체인 윤활 주기가 궁금해요", "138-ACC 에러코드 E12가 떠요"]:
        queries = pipeline.rewrite_query(question, glossary)
        ok = 1 <= len(queries) <= 3 and all(isinstance(q, str) and q.strip() for q in queries)
        failures += not ok
        print(f"[{'OK' if ok else 'MISMATCH'}] {question}")
        for q in queries:
            print(f"     - {q}")

    print("=" * 70)
    print("모두 통과" if not failures else f"확인 필요 {failures}건 (위 MISMATCH 항목)")
    raise SystemExit(1 if failures else 0)