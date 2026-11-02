"""
==============================================================================
 validator.py : 근거 검증  (담당: 이정빈 / WBS 3.5.4 / 기능 E-01 출처 표시)
==============================================================================

[이 파일이 하는 일]
  generate 가 받은 LLM 답(schemas.LLMAnswer 형식의 dict)을 화면에 내보내기 전에 걸러낸다.
  프롬프트로 "지어내지 마"라고 해도 LLM 이 틀릴 수 있으므로 코드로 한 번 더 확인한다.
    - validate_schema : LLMAnswer 다섯 칸 형식인지
    - check_language  : summary · causes · actions 가 한국어인지
    - verify_causes   : 원인 id 가 검색된 KB [자료]에 실제로 있고, 조치 개수가 KB 와 같은지 (아니면 제거)
    - verify_quote    : 인용문이 [자료] 원문에 그대로 있는지 (없으면 원문 문장으로 교체)
  LLM · DB 를 쓰지 않는다. 다시 요청할지는 이 파일이 아니라 RagPipeline.answer 가 정한다.

[answer 에서 쓰는 순서]
    validator = AnswerValidator()
    parsed = pipeline.generate(messages)["parsed"]
    if not (validator.validate_schema(parsed) and validator.check_language(parsed)):
        parsed = pipeline.generate(messages)["parsed"]        # 1회 재요청 (trace.schema_retries = 1)
        if not (validator.validate_schema(parsed) and validator.check_language(parsed)):
            ...                                               # 확인되지 않음(not_found) 응답
    checked = validator.verify_quote(validator.verify_causes(parsed, hits), hits)

  trace 에 남길 값은 검증 전(parsed)과 후(checked)를 비교해서 구한다.
    trace.removed_cause_ids   = parsed 에는 있고 checked 에는 없는 causes 의 id
    trace.replaced_quote_refs = quote 가 바뀐 sources 의 ref_id
  ※ checked 의 causes · actions 가 모두 비면 AnswerResponse(answered)를 만들 수 없으므로
    answer 가 확인되지 않음(not_found)으로 처리한다.
==============================================================================
"""
from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

from pydantic import ValidationError

from chatbot.core.schemas import CAUSE_ID_PATTERN, LLMAnswer
from chatbot.rag.prompts import KB_ACTION_SEPARATOR

if TYPE_CHECKING:
    from chatbot.core.schemas import Hit

# 한국어 판정 기준: 한 문장 안의 "한글 낱말 수 / (한글 + 영어 낱말 수)"가 이 값 이상이면 한국어로 본다.
# 글자 수가 아니라 낱말 수로 세는 이유: 영어 낱말은 한글보다 글자가 많아서, 부품명·모델명이 섞인
# 한국어 문장(예: "구동 체인(drive chain)에 ISO VG 220 오일을 바르세요")이 영어로 잘못 판정되기 때문
KOREAN_MIN_RATIO = 0.5

# 원문으로 바꿔 넣는 인용문의 최대 길이 (화면 카드에 한두 줄로 보이게)
MAX_QUOTE_CHARS = 300

_HANGUL_WORD = re.compile(r"[가-힣]+")
_LATIN_WORD = re.compile(r"[A-Za-z]+")
_MATCH_WORD = re.compile(r"[a-z0-9]+")              # 인용문과 원문 문장의 비슷한 정도를 잴 때 쓰는 낱말
_CAUSE_ID = re.compile(CAUSE_ID_PATTERN)             # 예: TS-HYTROL138ACC-T01-01#c1
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class AnswerValidator:
    """LLM 답(LLMAnswer dict)을 검사·정리한다. 상태를 갖지 않으므로 하나를 만들어 여러 요청에 같이 써도 된다."""

    def validate_schema(self, answer: dict):
        """
        pydantic 모델(schemas.LLMAnswer)로 LLM 답 형식 검사. 실패하면 answer 가 1회 재요청 후 not_found

        받는 것: generate 결과의 parsed (형식이 깨졌으면 None)
        돌려주는 것: bool (다섯 칸이 모두 있고 타입이 맞으면 True. 원인 id 형식도 여기서 검사된다)
        """
        if not isinstance(answer, dict):
            return False
        try:
            LLMAnswer.model_validate(answer)
        except ValidationError:
            return False
        return True

    def verify_causes(self, answer: dict, hits: list[Hit]):
        """
        답변의 원인 id가 검색된 KB에 있는지 확인하고 없으면 제거

        받는 것: LLM 답(dict), 검색 결과(build_prompt 에 넣은 hits)
        돌려주는 것: causes 를 정리한 새 dict (받은 dict 는 바꾸지 않는다)
        남기는 원인의 조건:
          - id 형식이 {kb_id}#c{순번} 이고
          - 그 kb_id 의 KB 가 검색 결과에 있고
          - 그 KB 의 [자료] 글에 그 id 가 그대로 적혀 있고 (LLM 이 본 글에 없는 id 는 지어낸 것)
          - 조치(actions) 개수가 KB 의 "조치 순서" 개수와 같다 (한국어로 옮기며 빼거나 더한 것을 막음)
            ※ [자료] 글에서 그 원인의 조치 순서를 찾지 못하면 개수 확인은 건너뛴다
        같은 id 가 여러 번 나오면 처음 하나만 남긴다.
        """
        result = copy.deepcopy(answer)
        kb_texts = {hit.id: hit.text for hit in hits or [] if hit.metadata.get("chunk_type") == "kb"}
        kept: list[dict] = []
        seen: set[str] = set()
        for cause in answer.get("causes") or []:
            cause_id = cause.get("id") or ""
            if cause_id in seen or not _CAUSE_ID.match(cause_id):
                continue
            text = kb_texts.get(cause_id.split("#", 1)[0])
            # (?!\d) : "…#c1" 이 "…#c10" 안에서 잘못 찾아지지 않게
            if text is None or not re.search(re.escape(cause_id) + r"(?!\d)", text):
                continue
            expected = _kb_action_count(text, cause_id)
            if expected is not None and len(cause.get("actions") or []) != expected:
                continue
            seen.add(cause_id)
            kept.append(copy.deepcopy(cause))
        result["causes"] = kept
        return result

    def verify_quote(self, answer: dict, hits: list[Hit]):
        """
        인용문이 원문의 부분 문자열인지 확인, 아니면 KB 원문 인용으로 교체

        받는 것: LLM 답(dict), 검색 결과(build_prompt 에 넣은 hits)
        돌려주는 것: sources · actions 를 정리한 새 dict (받은 dict 는 바꾸지 않는다)
          - ref_id 가 검색 결과에 없는 출처는 지운다 (LLM 이 지어낸 출처)
          - quote 가 그 자료 글에 그대로 없으면 그 자료에서 가장 비슷한 원문 문장으로 바꾼다
            (줄바꿈·공백 차이는 같은 것으로 본다)
          - ref_id 가 검색 결과에 없는 actions 단계도 지운다 (근거 없는 단계)
        """
        result = copy.deepcopy(answer)
        texts = {hit.id: hit.text for hit in hits or []}

        sources: list[dict] = []
        for source in answer.get("sources") or []:
            ref_id = source.get("ref_id")
            text = texts.get(ref_id)
            if text is None:
                continue
            quote = source.get("quote") or ""
            if not _contains(text, quote):
                quote = _closest_sentence(text, quote)
            item = {"ref_id": ref_id, "quote": quote}
            if item not in sources:
                sources.append(item)
        result["sources"] = sources
        result["actions"] = [step for step in answer.get("actions") or [] if step.get("ref_id") in texts]
        return result

    def check_language(self, answer: dict):
        """
        답변 언어가 한국어인지 확인, 아니면 answer 가 재요청

        받는 것: LLM 답(dict)
        돌려주는 것: bool (summary · causes 의 원인과 조치 · actions 의 문장이 모두 한국어면 True)
        sources 의 quote 는 영어 원문을 그대로 옮기는 칸이라 검사하지 않는다.
        """
        summary = (answer or {}).get("summary") or ""
        if not summary.strip():
            return False
        texts = [summary]
        for cause in answer.get("causes") or []:
            texts += [cause.get("cause") or "", *(cause.get("actions") or [])]
        texts += [step.get("text") or "" for step in answer.get("actions") or []]
        return all(_is_korean(text) for text in texts)


# ------------------------------------------------------------------------------
# 도우미 함수
# ------------------------------------------------------------------------------
def _is_korean(text: str) -> bool:
    """한글 낱말 비율이 KOREAN_MIN_RATIO 이상인지 본다. (숫자·기호는 세지 않는다)"""
    hangul = len(_HANGUL_WORD.findall(text))
    latin = len(_LATIN_WORD.findall(text))
    return hangul > 0 and hangul / (hangul + latin) >= KOREAN_MIN_RATIO


def _kb_action_count(text: str, cause_id: str) -> int | None:
    """[자료] 글에서 그 원인의 "조치 순서" 개수를 센다. 못 찾으면 None.

    prompts.KB_CAUSE 형식을 읽는다:  "- 원인 id {cause_id}: …" 다음 줄 "  조치 순서: A → B"
    """
    match = re.search(re.escape(cause_id) + r"(?!\d)[^\n]*\n[ \t]*조치 순서:[ \t]*([^\n]*)", text)
    if not match:
        return None
    return len([action for action in match.group(1).split(KB_ACTION_SEPARATOR.strip()) if action.strip()])


def _squash(text: str) -> str:
    """연속된 공백·줄바꿈을 공백 하나로 바꾼다."""
    return " ".join(text.split())


def _contains(text: str, quote: str) -> bool:
    """quote 가 text 의 부분 문자열인지 본다. 줄바꿈·공백 차이는 무시한다."""
    quote = _squash(quote)
    return bool(quote) and quote in _squash(text)


def _sentences(text: str) -> list[str]:
    """[자료] 글을 원문 문장 후보로 나눈다.

    줄 단위로 나눈 뒤 문장 끝(. ! ?)에서 한 번 더 나눈다.
    KB 글의 "조치 순서: …" 처럼 앞에 한글 이름표가 붙은 줄은 이름표를 떼고 원문 부분만 쓴다.
    """
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip(" -\t")
        label, sep, rest = line.partition(": ")
        if sep and _HANGUL_WORD.search(label):
            line = rest.strip()
        sentences += [s.strip() for s in _SENTENCE_END.split(line) if s.strip()]
    return sentences


def _closest_sentence(text: str, quote: str) -> str:
    """text 에서 quote 와 낱말이 가장 많이 겹치는 원문 문장을 고른다. 겹치는 문장이 없으면 첫 문장.

    돌려주는 문장은 항상 text 의 부분 문자열이다. (MAX_QUOTE_CHARS 를 넘으면 앞부분만)
    """
    candidates = _sentences(text) or [text.strip()]
    quote_words = set(_MATCH_WORD.findall(quote.lower()))

    def overlap(sentence: str) -> int:
        return len(quote_words & set(_MATCH_WORD.findall(sentence.lower())))

    best = max(candidates, key=overlap)   # 겹치는 수가 같으면 앞 문장이 남는다
    return best[:MAX_QUOTE_CHARS]
