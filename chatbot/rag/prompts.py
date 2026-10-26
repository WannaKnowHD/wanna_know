"""prompts.py : 챗봇에서 쓰는 고정 프롬프트 모음"""

PROMPT_VERSION = "v0"   # 프롬프트를 고치면 올리기 → 평가 결과에 같이 기록

# 답변 생성 (pipeline.py 의 build_prompt 에서 사용)
ANSWER_SYSTEM = """당신은 컨베이어 현장 정비 도우미입니다.
아래 [자료]에 있는 내용만 근거로 한국어로 답하세요.
원인은 자료에 있는 것만 id와 함께 적고, 자료에 없으면 not_found 로 답하세요.
안전 문구는 쓰지 마세요 (코드가 붙입니다)."""
ANSWER_HUMAN = "[자료]\n{context}\n\n질문: {question}"

# 검색어 변환 (rewrite_query)
REWRITE_QUERY = "..."

# 사진 (vision.py)
NAMEPLATE = "명판에서 모델명·시리얼·제조사를 보이는 그대로 읽어라 ..."
DAMAGE = "벨트 손상 사진을 hole / tear / joint / edge_damage / other 중 하나로 ..."

# 매뉴얼 표·그림 파싱 (document_processor.py)
TABLE_TO_MARKDOWN = "This is an OCR task. Transcribe the table into a markdown table ..."
FIGURE_DESCRIBE = "Describe this figure in English, including part names and callout numbers ..."