"""
==============================================================================
 prompts.py : 챗봇에서 쓰는 고정 프롬프트 모음  (위치: chatbot/rag/prompts.py)
==============================================================================
 - 버전은 둘로 나눠 관리한다. 고친 프롬프트에 맞는 버전만 올린다.
     PROMPT_VERSION        : 5번 표·그림 파싱. document_processor 가 비전 결과 캐시에 함께 저장하므로
                             올리면 저장된 표·그림 캐시가 모두 무효가 되어 비전 API 를 다시 부른다.
     ANSWER_PROMPT_VERSION : 3번 답변 생성. 고치면 올리고, 평가 결과(eval/results)에 같이 기록한다.
 - 출력 형식(JSON 칸)은 schemas.py 의 모델이 정한다. 여기에는 "무엇을 어떻게 채울지"만 쓴다.
 - 중괄호 {context} {question} 등은 쓰는 쪽에서 .format() 으로 채우는 자리다.
==============================================================================
"""

# 표·그림 파싱 프롬프트(5번) 버전. 5번을 고칠 때만 올린다 (머리말 참고)
PROMPT_VERSION = "v0"

# 답변 생성 프롬프트(3번) 버전. 팀 기준: 처음 완성된 챗봇 = v0, 이후 개선할 때마다 v1, v2 …
#   v0 : 첫 완성본 (LLMAnswer 기준. 원인·조치는 LLM 이 KB 를 한국어로 옮김)
#   v1 : 규칙 4 강화 — 원인 문장을 영어 그대로 복사해 한국어 검사에서 탈락하던 문제 (Fenner 벨트 KB 등).
#        "반드시 번역, 영어 원문은 sources 의 quote 에만" + 번역 예시 1줄
ANSWER_PROMPT_VERSION = "v1"


# ------------------------------------------------------------------------------
# 1. 질문 이해 (understand_question) → question_type
#    사용: QUESTION_TYPE.format(question=질문)
# ------------------------------------------------------------------------------
QUESTION_TYPE = """현장 정비 질문을 아래 넷 중 하나로 분류하세요.
- troubleshooting : 고장·이상 증상의 원인과 조치를 묻는 질문 (예: 모터가 뜨거워요, 벨트가 미끄러져요)
- maintenance     : 점검·윤활·교체 주기나 점검 항목을 묻는 질문 (예: 체인 윤활은 얼마마다?)
- procedure       : 설치·조정·교체 방법을 단계별로 묻는 질문 (예: 벨트 장력 조절 방법)
- out_of_scope    : 컨베이어 정비와 관계없는 질문 (예: 부품 가격, 잡담)

질문: {question}"""


# ------------------------------------------------------------------------------
# 2. 검색어 변환 (rewrite_query) → trace.queries_en
#    사용: REWRITE_QUERY.format(glossary=용어집_매칭, question=질문)
# ------------------------------------------------------------------------------
REWRITE_QUERY = """You convert a Korean field-maintenance question into English search queries
for conveyor manuals (Hytrol, Fenner Dunlop, PRECO).

Rules:
- Write 2 or 3 short English queries (3~8 words each).
- Use manual-style terms (e.g. "motor overheating", "belt tracking", "drive chain lubrication").
- Keep model names, error codes and numbers exactly as written (e.g. 138-ACC, E12).
- Glossary matches are given below. Prefer those terms.

Glossary: {glossary}
Question: {question}"""


# ------------------------------------------------------------------------------
# 3. 답변 생성 (build_prompt · generate) → schemas.LLMAnswer
#    LLM 은 LLMAnswer 다섯 칸(not_found · summary · causes · actions · sources)만 채운다.
#    원인·조치는 LLM 이 KB 영어 원문을 한국어로 옮겨 쓰고, id·조치 개수·순서가 KB 와 같은지는 validator 가 확인한다.
#    출처 문서명·쪽·그림·안전 문구는 코드가 KB 와 검색 결과에서 채운다 (AnswerResponse).
#    사용: system = ANSWER_SYSTEM
#          human  = ANSWER_HUMAN.format(context=자료, question_type=질문 유형, equipment=설비, question=질문)
# ------------------------------------------------------------------------------
ANSWER_SYSTEM = """당신은 물류 컨베이어 현장 정비를 돕는 도우미입니다.
사용자는 입사 1~2년 차 서비스 엔지니어이고, 답을 보고 현장에서 바로 따라 할 수 있어야 합니다.
[자료]는 제조사 매뉴얼(영어)에서 검색한 KB 항목과 본문 조각입니다.

[규칙]
1. [자료]에 있는 내용만 근거로 답하세요. 자료에 없는 원인·수치·절차는 만들지 마세요.
2. not_found
   - [자료]로 질문에 답할 수 없으면 true 로 하고, summary 는 "자료에서 확인되지 않습니다." 한 문장, causes·actions·sources 는 모두 빈 목록으로 두세요.
   - 답할 수 있으면 false 입니다.
3. summary : 한국어 1~2문장으로 쓰세요. 고장 질문이면 증상과 가장 먼저 확인할 원인을, 점검·절차 질문이면 주기나 핵심 단계를 씁니다.
4. causes : [질문 유형]이 troubleshooting 일 때만 채우세요. 원인 하나마다 id · cause · actions 를 씁니다.
   - id 는 [자료]에 적힌 "원인 id"를 한 글자도 바꾸지 말고 그대로 쓰세요 (예: TS-HYTROL138ACC-T01-01#c1). 새 id 를 만들지 마세요.
   - 질문한 증상과 관련 있는 원인만, 자료에 적힌 순서대로 쓰세요.
   - cause 는 그 원인 문장을, actions 는 "조치 순서"의 조치를 반드시 한국어로 번역해 쓰세요.
     영어 원문을 그대로 복사하면 안 됩니다. 영어 원문은 sources 의 quote 에만 씁니다.
     (예: "Conveyor is overloaded." → "컨베이어에 과부하가 걸려 있습니다.")
   - 조치는 빼거나 더하거나 합치지 말고, 자료와 같은 개수·같은 순서로 하나씩 옮기세요.
5. actions : [질문 유형]이 maintenance 또는 procedure 일 때만 채우세요. troubleshooting 이면 빈 목록입니다.
   - 한 단계씩 순서대로, 한국어 한 문장으로 쓰세요. 주기·기준값이 있으면 그 단계에 함께 쓰세요.
   - ref_id 는 그 단계의 근거가 된 [자료]의 id 입니다.
6. [질문 유형]이 "지정 안 됨"이면 근거로 쓴 자료가 고장 KB 일 때 causes 를, 그 밖의 자료일 때 actions 를 채우세요.
7. sources : 답에 쓴 [자료]마다 하나씩 쓰세요.
   - ref_id 는 [자료]의 id 입니다. causes 를 가져온 KB 의 id 와 actions 의 ref_id 는 모두 sources 에도 있어야 합니다.
   - quote 는 그 자료 본문에서 근거가 되는 영어 한 문장을 한 글자도 바꾸지 말고 그대로 복사하세요. 번역·요약·생략하지 마세요.
8. 수치·단위·모델명·알람 코드는 원문 그대로 쓰세요 (예: 2,080 hours, ISO VG 220, 138-ACC, E12).
9. [설비]가 지정되어 있으면 "적용 설비"가 그 설비이거나 "모든 모델"인 자료를 우선하세요.
10. 안전 문구(LOTO, 자격자 작업, 말림 주의 등)는 쓰지 마세요. 코드가 규칙에 따라 붙입니다.
11. 질문이 한국어가 아니어도 summary · causes · actions 는 한국어로 쓰세요."""

ANSWER_HUMAN = """[자료]
{context}

[질문 유형] {question_type}
[설비] {equipment}
질문: {question}"""

# [자료] 한 건의 형식. 검색 결과(Hit)마다 만들어 "\n\n" 으로 이어 붙인 것이 context 다.
#    사용: CONTEXT_ITEM.format(ref_id=hit.id, doc_id=hit.doc_id, page=hit.page, kind=종류 이름, text=hit.text)
#    ref_id 는 검색 결과 id (kb_id 또는 chunk_id). LLM 이 sources[].ref_id 로 그대로 돌려준다.
#    kind 는 CHUNK_TYPE_LABELS 에서 고른다. KB 는 metadata["kb_type"], 청크는 metadata["chunk_type"] 으로 찾는다.
#    (Hit.metadata 의 키 이름은 retriever.py 머리말의 "색인 약속"을 따른다)
CONTEXT_ITEM = """--- id: {ref_id} | 문서: {doc_id} p.{page} | 종류: {kind}
{text}"""

# [자료]의 종류 이름. "고장 KB" 는 ANSWER_SYSTEM 규칙 6 과 같은 말이어야 한다.
CHUNK_TYPE_LABELS = {
    "troubleshooting": "고장 KB",
    "maintenance": "점검 KB",
    "text": "본문",
    "table": "표",
    "figure": "그림",
}

# KB 항목을 글로 바꾸는 틀. KB JSON 을 그대로 넣지 않고 사람이 읽는 글로 바꿔
# 토큰을 줄이고, 원인 id 를 원인 바로 앞에 붙여 LLM 이 id 를 정확히 옮겨 쓰게 한다.
# 색인(build_index)에서 KB 의 text 를 이 틀로 만들면 검색에 쓰는 글과 LLM 이 보는 글이 같아진다.
#    고장 KB : KB_TROUBLESHOOTING.format(applies_to=..., symptom=..., alarm_codes=..., causes=...)
#              causes 는 원인마다 KB_CAUSE 를 채워 "\n" 으로 이어 붙인 것
#              KB_CAUSE 의 actions 는 조치들을 KB_ACTION_SEPARATOR 로 이어 붙인 것
#              alarm_codes 는 KB_ALARM_CODES 를 채운 것 (알람 코드가 없으면 "")
#    점검 KB : KB_MAINTENANCE.format(applies_to=..., task=..., interval=..., extra=...)
#              extra 는 KB_CRITERIA · KB_DETAILS 중 값이 있는 것만 이어 붙인 것 (없으면 "")
KB_TROUBLESHOOTING = """적용 설비: {applies_to}
증상: {symptom}{alarm_codes}
{causes}"""

KB_ALARM_CODES = " (알람 코드: {codes})"

KB_CAUSE = """- 원인 id {cause_id}: {cause}
  조치 순서: {actions}"""
# 조치를 잇는 기호. validator 가 이 기호로 KB 조치 개수를 센다 (LLM 이 옮긴 조치 개수와 비교)
KB_ACTION_SEPARATOR = " → "

KB_MAINTENANCE = """적용 설비: {applies_to}
작업: {task}
주기: {interval}{extra}"""

KB_CRITERIA = "\n판정 기준: {criteria}"
KB_DETAILS = "\n세부 내용: {details}"

# 빈 자리에 넣는 값
NO_EQUIPMENT = "지정 안 됨"        # 설비를 모를 때 {equipment}
NO_QUESTION_TYPE = "지정 안 됨"    # 질문 유형을 모를 때 {question_type} (ANSWER_SYSTEM 규칙 6 과 같은 말)
ALL_MODELS_TEXT = "모든 모델"      # KB applies_to 가 ["*"] 일 때 {applies_to} (ANSWER_SYSTEM 규칙 9 와 같은 말)
NO_CONTEXT = "(검색된 자료 없음)"  # 검색 결과가 하나도 없을 때 {context} (LLM 은 규칙 2 에 따라 not_found 로 답한다)


# ------------------------------------------------------------------------------
# 4. 사진 (chatbot/vision/)
# ------------------------------------------------------------------------------
# 4-1. 사진 판별 (classify_image) → kind · confidence
IMAGE_CLASSIFY = """This is an image classification task for conveyor maintenance photos.
Classify the photo into exactly one kind:
- nameplate   : a metal or sticker label showing model, serial number or manufacturer
- belt_damage : a conveyor belt with visible damage (hole, tear, joint/splice problem, edge damage)
- other       : anything else (people, unrelated objects, blurry or unreadable photos)
Give a confidence between 0 and 1."""

# 4-2. 명판 판독 (read_nameplate) → raw_text · serial · maker
#      matched_model · match 는 코드(match_model)가 models.csv 와 비교해 채운다
NAMEPLATE = """This is an OCR task. Read the conveyor nameplate exactly as printed.
- raw_text : the model name exactly as written (keep spaces and hyphens, e.g. "138 ACC")
- serial   : the serial number, or null if not visible
- maker    : the manufacturer name, or null if not visible
Do not guess or correct any characters. If a field is unreadable, use null."""

# 4-3. 손상 사진 분석 (describe_damage) → label · symptom_ko · confidence
DAMAGE = """This is a visual inspection task for a conveyor belt photo.
Choose one label:
- hole        : a puncture or hole through the belt
- tear        : a rip or cut along or across the belt
- joint       : a problem at the splice/joint (separation, lifted fasteners)
- edge_damage : worn, frayed or broken belt edges
- other       : damage that does not fit the labels above
Write symptom_ko as one short Korean sentence a field engineer would say
(e.g. "벨트 가장자리가 닳고 찢어졌어요").
Give a confidence between 0 and 1. If the damage is unclear, use a low confidence."""


# ------------------------------------------------------------------------------
# 5. 매뉴얼 표·그림 파싱 (document_processor.py · crop_regions)
# ------------------------------------------------------------------------------
TABLE_TO_MARKDOWN = """This is an OCR task. Transcribe the table in this image into a markdown table.
- Copy every cell exactly as printed (text, numbers, units). Do not translate, summarize or interpret.
- Keep the original row and column order and header names.
- For merged cells, repeat the value in every row it covers.
- Leave empty cells empty.
After the table, add one line starting with "Summary:" describing what the table is about in English."""

FIGURE_DESCRIBE = """Describe this figure from a conveyor maintenance manual in English.
- Start with the figure label if printed (e.g. "FIGURE 21A").
- List every part name and callout number that is visible, and what each one points to.
- Describe any adjustment direction, measurement or value shown (e.g. arrows, dimensions).
- Only describe what is visible. Do not add advice or information that is not in the figure."""