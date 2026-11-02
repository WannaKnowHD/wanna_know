"""
==============================================================================
 llm.py : LLM 클라이언트  (담당: 박세람)
==============================================================================

[이 파일이 하는 일]
  OpenAI 호출을 한곳에서 처리한다. (수업에서 배운 ChatOpenAI / OpenAIEmbeddings 사용)
    - chat   : 텍스트 답변
    - vision : 사진 + 질문 → 답 (명판 판독, 손상 사진, 매뉴얼 표·그림 복원)
    - embed  : 문장 → 벡터 (검색용)

[규칙] (기능명세서 llm.py 행)
  - temperature=0       : 같은 질문엔 항상 같은 답 (평가 점수를 비교하려면 꼭 필요)
  - timeout=30          : 30초 안에 답이 없으면 실패로 본다
  - max_retries=3       : 실패하면 기다리는 시간을 늘려 가며 3번까지 다시 시도한다
  - 호출마다 걸린 시간을 출력하고, 결과에도 담아 준다
  - 키·모델명은 코드에 쓰지 않고 Settings 에서 받는다

[사용 예]
    from chatbot.core.config import Settings
    from chatbot.rag.llm import LLMClient

    llm = LLMClient(Settings.load())
    result = llm.chat([{"role": "user", "content": "안녕하세요"}])
    print(result["text"])
==============================================================================
"""
import base64      # 사진 → 글자로 바꾸기 (data URL 만들 때)
import json        # 임베딩 캐시를 파일로 저장·불러오기
import os          # 캐시 파일이 있는지 확인
import time        # 걸린 시간 재기

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# 비전 모델이 작업을 거부했는지 확인할 때 찾는 말
REFUSAL_WORDS = ["can't assist", "cannot assist", "sorry", "죄송"]

# 거부했을 때 다시 보낼 영어 지시문 ("OCR 작업"이라고 분명히 말하면 잘 거부하지 않는다)
RETRY_PROMPT = ("This is an OCR task. Transcribe exactly what you see in the image "
                "(text, numbers, table cells) without interpretation or advice.")


class LLMClient:
    """OpenAI 텍스트·사진·임베딩 호출을 한곳에서 처리한다."""

    def __init__(self, settings, cache_path=None):
        """
        받는 것:
          settings   : config.py 의 Settings (키·모델명을 여기서 꺼낸다)
          cache_path : 임베딩 캐시 파일 경로. 비우면 index 폴더 아래 embed_cache.json
                       "" (빈 문자열) 을 넣으면 파일에 저장하지 않는다 (테스트용)
        """
        self.settings = settings

        # (1) 답변용 LLM
        self.text_llm = self.make_llm(settings.model_text)

        # (2) 사진용 LLM (모델만 다르고 규칙은 같다)
        self.vision_llm = self.make_llm(settings.model_vision)

        # (3) 검색용 임베딩
        self.embeddings = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
            max_retries=3,
        )

        # (4) 임베딩 캐시 : {문장: 벡터}
        #     같은 문장을 다시 임베딩하지 않으려고 저장해 둔다 (시간·비용 절약)
        if cache_path is None:
            cache_path = os.path.join(str(settings.chroma_dir), f"embed_cache_{settings.embedding_model}.json")
        self.cache_path = cache_path
        self.cache = self.load_cache()

    def make_llm(self, model):
        """규칙(temperature=0, 30초, 재시도 3회)이 들어간 ChatOpenAI 를 만든다."""
        return ChatOpenAI(
            model=model,
            temperature=0,
            timeout=30,
            max_retries=3,
            api_key=self.settings.openai_api_key,
        )

    # ==========================================================================
    # 1. chat : 텍스트 답변
    # ==========================================================================
    def chat(self, messages, model=None, json_schema=None):
        """텍스트 모델을 호출한다.

        받는 것:
          messages    : [{"role": "system" 또는 "user", "content": "..."}, ...]
          model       : 비우면 MODEL_TEXT 를 쓴다 (모델 비교 실험 때만 넣는다)
          json_schema : 넣으면 답을 이 형식으로 받는다
                        - pydantic 모델 (예: schemas.py 의 LlmAnswer) ← 권장
                        - 또는 JSON 스키마 dict
        돌려주는 것 (dict):
          text       : 답 글자
          parsed     : 형식에 맞춰 받은 답을 dict 로 (json_schema 를 안 넣었거나 형식이 깨지면 None)
          model      : 사용한 모델 이름
          latency_ms : 걸린 시간 (밀리초, 정수)  → trace.latency_ms 에 그대로 넣을 수 있음
        """
        llm = self.text_llm
        if model is not None and model != self.settings.model_text:
            llm = self.make_llm(model)
        return self.call(llm, messages, json_schema, "chat")

    # ==========================================================================
    # 2. vision : 사진 + 질문
    # ==========================================================================
    def vision(self, image, prompt, json_schema):
        """사진과 지시문을 비전 모델에 보내고 JSON 결과를 받는다.

        받는 것:
          image       : 사진 파일 내용 (bytes)  예) open("명판.png", "rb").read()
          prompt      : 지시문  예) "명판에서 모델명을 읽어라"
          json_schema : 결과 형식 (pydantic 모델 또는 JSON 스키마 dict)
                        예) schemas.py 의 NameplateResult, DamageResult
        돌려주는 것: chat 과 같은 dict
        """
        if not image:
            raise ValueError("사진이 비어 있습니다")

        # (1) 사진 → base64 글자 → data URL
        img_b64 = base64.b64encode(image).decode()
        data_url = f"data:{self.image_type(image)};base64,{img_b64}"

        # (2) 글자 + 사진을 한 메시지에 담는다 (멀티모달 메시지)
        message = HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
        ])
        result = self.call(self.vision_llm, [message], json_schema, "vision")

        # (3) 거부 응답이면 영어 OCR 지시문을 앞에 붙여 1번만 다시 시도
        if self.is_refusal(result["text"]):
            print("[llm] vision 거부 응답 → 영어 지시문으로 재시도")
            message = HumanMessage(content=[
                {"type": "text", "text": RETRY_PROMPT + "\n\n" + prompt},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ])
            result = self.call(self.vision_llm, [message], json_schema, "vision 재시도")
        return result

    # ==========================================================================
    # 3. embed : 문장 → 벡터
    # ==========================================================================
    def embed(self, texts):
        """문장 목록을 벡터 목록으로 바꾼다. (넣은 순서 그대로 돌려준다)

        캐시에 있는 문장은 꺼내 쓰고, 처음 보는 문장만 OpenAI 를 부른다.
        """
        if not texts:
            return []

        # (1) 캐시에 없는 문장만 고른다 (같은 문장이 여러 번 있어도 한 번만)
        new_texts = []
        for text in texts:
            if text not in self.cache and text not in new_texts:
                new_texts.append(text)

        # (2) 새 문장만 임베딩해서 캐시에 넣고 파일에 저장
        if new_texts:
            start = time.time()
            vectors = self.embeddings.embed_documents(new_texts)
            for text, vector in zip(new_texts, vectors):
                self.cache[text] = vector
            self.save_cache()
            print(f"[llm] embed: 새로 {len(new_texts)}개 · 캐시 {len(texts) - len(new_texts)}개 · "
                  f"{(time.time() - start) * 1000:.0f} ms")

        # (3) 넣은 순서대로 캐시에서 꺼내 돌려준다
        return [self.cache[text] for text in texts]

    # ==========================================================================
    # 공통 : 모델 호출 + 시간 기록
    # ==========================================================================
    def call(self, llm, messages, json_schema, name):
        """LLM 을 부르고, 걸린 시간을 함께 돌려준다."""
        start = time.time()

        if json_schema is None:
            # 그냥 호출 : 답은 .content
            response = llm.invoke(messages)
            parsed = None
        else:
            # JSON 형식으로 받기
            #   include_raw=True → 형식에 맞춘 답(parsed) + 원래 답(raw, 토큰 정보가 들어 있음)을 같이 준다
            structured_llm = llm.with_structured_output(
                self.to_schema(json_schema), method="json_schema", include_raw=True)
            output = structured_llm.invoke(messages)
            response = output["raw"]
            parsed = output["parsed"]
            if hasattr(parsed, "model_dump"):
                # pydantic 모델로 받은 경우 → dict 로 바꿔서 돌려준다 (돌려주는 형식을 항상 dict 로 통일)
                parsed = parsed.model_dump()
            if output["parsing_error"] is not None:
                # 형식이 깨져도 멈추지 않고 None 으로 돌려준다 (다시 요청할지는 validator.py 가 정한다)
                print(f"[llm] {name}: JSON 형식이 깨졌습니다")
                parsed = None

        latency_ms = int((time.time() - start) * 1000)
        print(f"[llm] {name}: {llm.model_name} · {latency_ms} ms")

        return {
            "text": response.content if isinstance(response.content, str) else str(response.content),
            "parsed": parsed,
            "model": llm.model_name,
            "latency_ms": latency_ms,
        }

    # ==========================================================================
    # 도우미
    # ==========================================================================
    def to_schema(self, json_schema):
        """LangChain 에 넘길 형식으로 맞춘다.

        - pydantic 모델이면 그대로 넘긴다 (LangChain 이 알아서 JSON 스키마로 바꿈)
        - {"name": ..., "schema": {...}} 형식이면 안쪽 schema 를 꺼내고 이름을 title 로 붙인다
        """
        if isinstance(json_schema, type):
            return json_schema
        if "schema" in json_schema:
            schema = dict(json_schema["schema"])
            schema["title"] = json_schema.get("name", "answer")
            return schema
        schema = dict(json_schema)
        schema.setdefault("title", "answer")
        return schema

    def is_refusal(self, text):
        """비전 모델이 '도와드릴 수 없습니다'로 거부했는지 확인한다."""
        text = text.lower()
        return any(word in text for word in REFUSAL_WORDS)

    def image_type(self, image):
        """사진 파일 맨 앞 몇 글자(파일 서명)를 보고 형식을 알아낸다."""
        if image[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if image[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
            return "image/webp"
        raise ValueError("PNG·JPG·WEBP 사진만 넣을 수 있습니다")

    def load_cache(self):
        """캐시 파일이 있으면 불러오고, 없으면 빈 dict 로 시작한다."""
        if self.cache_path and os.path.exists(self.cache_path):
            with open(self.cache_path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_cache(self):
        """캐시를 파일에 저장한다. (cache_path 가 "" 이면 저장하지 않음)"""
        if not self.cache_path:
            return
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f)