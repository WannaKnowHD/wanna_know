"""질문 1건 처리 파이프라인 (검색 → 생성 → 검증). 실행 파일 아님"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from chatbot.core.schemas import Model, QueryInfo
from chatbot.rag.prompts import QUESTION_TYPE, REWRITE_QUERY

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


class RagPipeline:
    """RagPipeline (기능명세서 기준 뼈대)."""

    def __init__(self, llm):
        """
        받는 것:
          llm : rag.llm.LLMClient (질문 이해·검색어 변환에서 chat 을 호출)
        """
        self.llm = llm

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
            values = glossary[term]                 # 영문 용어는 문자열 하나일 수도, 목록일 수도 있음 → 목록으로 통일하고 빈 값은 버림
            english = [v.strip() for v in ([values] if isinstance(values, str) else values or []) if v and v.strip()]
            if english:
                matched[term] = english

        # 2) 용어집에 걸린 표현은 영문 용어를 정해서 LLM 에게 주고(REWRITE_QUERY 가 "이 용어를 우선 쓰라"고 함), 나머지만 LLM 이 바꿈
        glossary_text = "\n".join(f"{k} → {', '.join(v)}" for k, v in matched.items()) or "(none)"
        queries: list[str] = []
        try:
            text = self.llm.chat(REWRITE_QUERY.format(glossary=glossary_text, question=question))["text"]
            # 프롬프트가 한 줄에 검색어 하나씩 쓰게 하므로 줄 단위로 읽음 ("1. ", "- " 같은 목록 기호·따옴표는 지움)
            queries = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip(" \"'`") for line in text.splitlines()]
        except Exception as e:  # LLM 실패: 용어집 결과만으로 검색을 계속함
            print(f"[pipeline] 검색어 변환 실패 → 용어집만 사용: {type(e).__name__}: {e}")
        queries = [q for q in queries if q]

        # 3) LLM 이 용어집 용어를 하나도 쓰지 않았다면, 용어집의 영문 용어로 만든 검색어를 맨 앞에 둠
        if matched and not any(t.lower() in q.lower() for q in queries for v in matched.values() for t in v):
            queries.insert(0, " ".join(v[0] for v in matched.values()))
            
        # 대소문자만 다른 검색어는 하나만 남김 ("Motor Overheating" = "motor overheating")
        unique: list[str] = []
        for q in queries:
            if q.lower() not in (u.lower() for u in unique):
                unique.append(q)
        # 검색어는 2~3개(REWRITE_QUERY). 모두 실패해도 검색은 계속할 수 있게 원래 질문이라도 돌려줌
        return unique[:3] or [question]

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