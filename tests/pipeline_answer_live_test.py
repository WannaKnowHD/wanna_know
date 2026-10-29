"""RagPipeline.answer 실제 연결 확인 (WBS 3.5.6): 진짜 OpenAI · 검색 색인 · 리랭커 · MySQL(Aiven)

- 질문 2개만 보낸다 (LLM 5~6번 호출, 비용 발생)
- 질문이 RAG 를 지나가는 단계(질문 이해 → 검색어 → 검색 → 생성 → 검증 → 응답)를 순서대로 출력한다
- 로그는 source="eval" 로 남겨 대시보드에 섞이지 않게 하고, 끝나면 지운다 (질문이 "[TEST answer]" 로 시작)
- 색인(chroma_db/, bm25.pkl)이 없으면 python build_index.py 를 먼저 실행

실행 (환경변수 LIVE_RAG=1 일 때만 돈다. 평소 pytest 에서는 건너뜀):
    Windows cmd : set "LIVE_RAG=1" && python -m pytest tests/pipeline_answer_live_test.py -v -s
    Git Bash    : LIVE_RAG=1 python -m pytest tests/pipeline_answer_live_test.py -v -s
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chatbot.core.schemas import AnswerResponse
from chatbot.pipeline import RagPipeline, load_kb

pytestmark = pytest.mark.skipif(os.environ.get("LIVE_RAG", "").strip() != "1",
                                reason="실제 연결 확인은 LIVE_RAG=1 일 때만")

TAG = "[TEST answer]"


@pytest.fixture(scope="module")
def db():
    from chatbot.core.config import Settings
    from chatbot.db.db import Database

    db = Database().connect(Settings.load())
    _cleanup(db)                     # 지난번에 실패해서 남은 테스트 행이 있으면 먼저 지움
    yield db
    _cleanup(db)
    db.close()


@pytest.fixture(scope="module")
def pipeline(db):
    from chatbot.core.config import Settings
    from chatbot.core.logger import RequestLogger
    from chatbot.core.safety import SafetyRules
    from chatbot.rag.glossary import Glossary
    from chatbot.rag.llm import LLMClient
    from chatbot.rag.retriever import Retriever

    settings = Settings.load()
    llm = LLMClient(settings)
    return RagPipeline(llm, retriever=Retriever.from_settings(settings, llm), safety=SafetyRules(),
                       request_logger=RequestLogger(db), glossary=Glossary().load_glossary("data/glossary.csv"),
                       kb=load_kb(ROOT / "kb"), log_source="eval")


def show(out):
    """질문이 RAG 를 지나가는 단계를 순서대로 출력"""
    t = out["trace"]
    print("\n① 질문 이해 :", out["question_type"], "/ 설비:", (out["equipment"] or {}).get("model"))
    print("② 검색어     :", t["queries_en"])
    print("③ 검색 결과  : 1위", t["top_score"], "/ 기준", t["threshold"], "/ 설비 필터 풀림:", out["filter_released"])
    for hit in t["hits"]:
        print(f"     {hit['score']:.3f}  {hit['id']}  ({hit['doc_id']} p.{hit['page']})")
    print("④ 생성       : 토큰", t["tokens"], "(호출마다 위의 [llm] 줄 참고)")
    print("⑤ 검증       : 재요청", t["schema_retries"], "/ 지운 원인", t["removed_cause_ids"],
          "/ 바꾼 인용", t["replaced_quote_refs"])
    print("⑥ 응답       :", out["status"], "-", out["summary"] or out["message"])
    for cause in out["causes"]:
        print(f"     원인 {cause['id']}: {cause['cause']} → {cause['actions']}")
    for step in out["actions"]:
        print(f"     조치: {step['text']} ({step['ref_id']})")
    for source in out["sources"]:
        print(f"     출처: {source['doc_title']} p.{source['page']} \"{source['quote'][:80]}\"")
    print("     안전 문구:", [n["rule_id"] for n in out["safety"]])
    print("시간(ms)     :", t["latency_ms"])
    if t["error"]:
        print("오류         :", t["error"])


def test_troubleshooting_question_is_answered(pipeline):
    out = pipeline.answer(f"{TAG} 138-ACC 모터가 너무 뜨거워요", {}, "text")
    show(out)
    AnswerResponse.model_validate(out)
    assert out["status"] == "answered", "정상 질문이 답변되지 않음 → 위 ③ 점수 · 오류 확인"
    assert out["causes"] and out["safety"]
    assert out["trace"]["removed_cause_ids"] == []      # 색인 KB 글에 원인 id 가 있어야 지워지지 않음


def test_out_of_scope_and_log_saved(pipeline, db):
    out = pipeline.answer(f"{TAG} 오늘 점심 메뉴 추천해 줘", {}, "text")
    show(out)
    assert out["status"] == "not_found"
    rows = db.fetch("SELECT source, question, not_found FROM chat_logs WHERE question LIKE %s", (f"{TAG}%",))
    assert {r["source"] for r in rows} == {"eval"} and len(rows) == 2


def _cleanup(db):
    """테스트 행만 지운다 (질문이 TAG 로 시작하는 로그)"""
    db.execute("DELETE FROM chat_logs WHERE question LIKE %s", (f"{TAG}%",))