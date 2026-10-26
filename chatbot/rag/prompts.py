"""
==============================================================================
 prompts.py : 챗봇에서 쓰는 고정 프롬프트 모음  (위치: chatbot/rag/prompts.py)
==============================================================================
 - 프롬프트를 고치면 PROMPT_VERSION 을 올리고, 평가 결과(eval/results)에 같이 기록한다.
 - 출력 형식(JSON 칸)은 schemas.py 의 모델이 정한다. 여기에는 "무엇을 어떻게 채울지"만 쓴다.
 - 중괄호 {context} {question} 등은 쓰는 쪽에서 .format() 으로 채우는 자리다.
==============================================================================
"""

PROMPT_VERSION = "v0"


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
# 3. 답변 생성 (build_prompt · generate) → LlmAnswer
#    사용: system = ANSWER_SYSTEM
#          human  = ANSWER_HUMAN.format(context=자료, equipment=설비, question=질문)
# ------------------------------------------------------------------------------
ANSWER_SYSTEM = """당신은 물류 컨베이어 현장 정비를 돕는 도우미입니다.
사용자는 입사 1~2년 차 서비스 엔지니어입니다.

[규칙]
1. 아래 [자료]에 있는 내용만 근거로 답하세요. 자료에 없는 원인·수치·절차는 만들지 마세요.
2. 자료로 답할 수 없으면 not_found 를 true 로 하고, summary 에 "자료에서 확인되지 않습니다"라고 쓰고, 나머지 목록은 비워 두세요.
3. summary : 증상 요약·주요 원인·우선 할 일을 한국어 1~2문장으로 쓰세요.
4. causes  : 고장 질문(troubleshooting)일 때만 채우세요.
   - id 는 [자료]에 적힌 원인 id 를 그대로 쓰세요 (예: hytrol_138acc_ts_04#c1). 새 id 를 만들지 마세요.
   - 순서는 자료의 표 순서를 따르세요.
   - cause · action 은 한국어로 쓰세요.
5. actions : 점검·절차 질문(maintenance · procedure)일 때만 채우세요.
   - 한 단계씩 순서대로 쓰고, 점검 주기가 있으면 함께 쓰세요.
   - ref_id 는 그 단계의 근거가 된 [자료]의 id 를 쓰세요.
6. sources : 답에 쓴 근거마다 하나씩 쓰세요.
   - ref_id 는 [자료]의 id, quote 는 그 자료의 영어 원문을 한 글자도 바꾸지 말고 그대로 옮기세요.
7. 수치·단위는 원문 그대로 쓰세요 (예: 2,080시간, 6개월, ISO VG 220).
8. 안전 문구(LOTO, 자격자 작업 등)는 쓰지 마세요. 코드가 규칙에 따라 붙입니다.
9. follow_ups 는 빈 목록으로 두세요."""

ANSWER_HUMAN = """[자료]
{context}

[설비] {equipment}
질문: {question}"""

# [자료] 한 건의 형식. 검색 결과마다 만들어 "\n\n" 으로 이어 붙인 것이 context 다.
#    사용: CONTEXT_ITEM.format(ref_id=..., doc_title=..., page=..., chunk_type=..., text=...)
CONTEXT_ITEM = """--- id: {ref_id} | 문서: {doc_title} p.{page} | 종류: {chunk_type}
{text}"""

# 설비를 모를 때 {equipment} 자리에 넣는 값
NO_EQUIPMENT = "지정 안 됨"


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