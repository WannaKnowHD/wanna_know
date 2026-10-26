"""
llm.py 테스트 (실제 OpenAI 호출 없음)

- 가짜 OpenAI 서버를 내 PC(127.0.0.1)에 띄우고 ChatOpenAI 가 그곳을 부르게 한다.
  → LangChain 의 실제 동작(JSON 형식 강제, 토큰 정보, 재시도)을 그대로 확인할 수 있다.
실행: pytest tests/llm_test.py
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from chatbot.rag.llm import LLMClient

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


# ------------------------------------------------------------------------------
# 가짜 OpenAI 서버
# ------------------------------------------------------------------------------
class FakeOpenAI(BaseHTTPRequestHandler):
    replies: list = []      # 차례대로 돌려줄 답 (문자열 = 정상 답, 숫자 = 그 HTTP 오류)
    requests: list = []     # 서버가 받은 요청 기록

    def log_message(self, *args):       # 테스트 출력 깔끔하게
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.requests.append((self.path, body))
        if self.path.endswith("/embeddings"):
            data = [{"object": "embedding", "index": i, "embedding": [float(len(t)), 1.0]}
                    for i, t in enumerate(body["input"])]
            return self._send(200, {"object": "list", "data": data, "model": body["model"],
                                    "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        reply = FakeOpenAI.replies.pop(0)
        if isinstance(reply, int):
            return self._send(reply, {"error": {"message": "fake error", "type": "server_error"}})
        self._send(200, {
            "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    def _send(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(scope="module")
def base_url():
    server = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def make_client(base_url, cache_path="", chroma_dir="unused"):
    """가짜 서버를 부르는 LLMClient (make_llm 만 바꿔서 base_url 을 붙인다)"""
    class TestClient(LLMClient):
        def make_llm(self, model):
            return ChatOpenAI(model=model, temperature=0, timeout=5, max_retries=3,
                              api_key="sk-test", base_url=base_url)
    settings = SimpleNamespace(openai_api_key="sk-test", model_text="text-model",
                               model_vision="vision-model", embedding_model="emb-model", chroma_dir=chroma_dir)
    c = TestClient(settings, cache_path=cache_path)
    c.embeddings = OpenAIEmbeddings(model="emb-model", api_key="sk-test", base_url=base_url,
                                    check_embedding_ctx_length=False)
    return c


@pytest.fixture
def client(base_url):
    FakeOpenAI.replies, FakeOpenAI.requests = [], []
    return make_client(base_url)


SCHEMA = {"name": "answer", "schema": {
    "type": "object", "properties": {"summary": {"type": "string"}},
    "required": ["summary"], "additionalProperties": False}}


# ------------------------------------------------------------------------------
# chat
# ------------------------------------------------------------------------------
def test_chat_text(client):
    FakeOpenAI.replies = ["안녕하세요"]
    out = client.chat([{"role": "user", "content": "안녕"}])
    assert out["text"] == "안녕하세요" and out["parsed"] is None
    assert out["usage"]["total_tokens"] == 15 and out["model"] == "text-model"


def test_chat_json_schema(client):
    FakeOpenAI.replies = ['{"summary": "과부하 확인"}']
    out = client.chat([{"role": "user", "content": "모터 과열"}], json_schema=SCHEMA)
    assert out["parsed"] == {"summary": "과부하 확인"}
    sent = FakeOpenAI.requests[-1][1]
    assert sent["response_format"]["type"] == "json_schema" and sent["temperature"] == 0


def test_chat_bad_json_gives_none(client):
    FakeOpenAI.replies = ["형식이 깨진 답"]
    assert client.chat([{"role": "user", "content": "x"}], json_schema=SCHEMA)["parsed"] is None


def test_chat_retries_on_server_error(client):
    FakeOpenAI.replies = [500, 500, "복구됨"]          # 2번 실패 후 성공
    assert client.chat([{"role": "user", "content": "x"}])["text"] == "복구됨"
    assert len(FakeOpenAI.requests) == 3


def test_chat_other_model(client):
    FakeOpenAI.replies = ["ok"]
    client.chat([{"role": "user", "content": "x"}], model="other-model")
    assert FakeOpenAI.requests[-1][1]["model"] == "other-model"


# ------------------------------------------------------------------------------
# vision
# ------------------------------------------------------------------------------
def test_vision_sends_image(client):
    FakeOpenAI.replies = ['{"summary": "138ACC"}']
    out = client.vision(PNG, "명판을 읽어라", SCHEMA)
    content = FakeOpenAI.requests[-1][1]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["image_url"]["detail"] == "high"
    assert out["parsed"] == {"summary": "138ACC"} and out["model"] == "vision-model"


def test_vision_retries_once_on_refusal(client):
    FakeOpenAI.replies = ["Sorry, I can't assist with that.", '{"summary": "E12"}']
    out = client.vision(PNG, "표를 옮겨 적어라", SCHEMA)
    assert out["parsed"] == {"summary": "E12"}
    assert "OCR task" in FakeOpenAI.requests[-1][1]["messages"][0]["content"][0]["text"]


def test_vision_rejects_bad_image(client):
    with pytest.raises(ValueError):
        client.vision(b"not an image", "x", SCHEMA)


# ------------------------------------------------------------------------------
# embed
# ------------------------------------------------------------------------------
def test_embed_order_dedupe_cache(client):
    v = client.embed(["a", "bb", "a"])
    assert v[0] == v[2] and v[1][0] == 2.0
    client.embed(["bb", "ccc"])
    sent = [b["input"] for p, b in FakeOpenAI.requests if p.endswith("/embeddings")]
    assert sent == [["a", "bb"], ["ccc"]]          # 중복·캐시 문장은 다시 안 부름


def test_embed_cache_file_survives_restart(base_url, tmp_path):
    FakeOpenAI.requests = []
    first = make_client(base_url, cache_path=None, chroma_dir=str(tmp_path)).embed(["x", "y"])
    second = make_client(base_url, cache_path=None, chroma_dir=str(tmp_path)).embed(["y", "x"])  # 재실행
    assert second == [first[1], first[0]]
    assert len([p for p, _ in FakeOpenAI.requests if p.endswith("/embeddings")]) == 1
    assert (tmp_path / "embed_cache_emb-model.json").exists()


# ------------------------------------------------------------------------------
# 도우미
# ------------------------------------------------------------------------------
def test_helpers(client):
    assert client.image_type(PNG) == "image/png"
    assert client.is_refusal("I'm sorry, I can't assist") and not client.is_refusal('{"model": "138ACC"}')


# ------------------------------------------------------------------------------
# pydantic 모델 (schemas.py 의 LlmAnswer 같은 형식)
# ------------------------------------------------------------------------------
from pydantic import BaseModel, ConfigDict  # noqa: E402


class Cause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    cause: str
    action: str


class LlmAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    not_found: bool
    summary: str
    causes: list[Cause]


def test_chat_pydantic_model(client):
    FakeOpenAI.replies = ['{"not_found": false, "summary": "과부하 확인", '
                          '"causes": [{"id": "hytrol_138acc_ts_04#c1", "cause": "과부하", "action": "적재량 줄이기"}]}']
    out = client.chat([{"role": "user", "content": "모터 과열"}], json_schema=LlmAnswer)
    assert out["parsed"]["causes"][0]["id"] == "hytrol_138acc_ts_04#c1"   # dict 로 돌아옴
    sent = FakeOpenAI.requests[-1][1]["response_format"]["json_schema"]
    assert sent["schema"]["additionalProperties"] is False                # extra="forbid" 반영
    assert isinstance(out["latency_ms"], int)