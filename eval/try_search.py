"""질문 하나로 검색 단계별 상위 5개를 출력한다 (WBS 3.4.3 검색 결과 캡처용, 레포에 올리지 않음).

레포의 eval\\ 폴더에 복사해 두고(커밋하지 않음) 레포 폴더에서 실행:
    cd C:\\workspaces\\wanna_know
    python eval\\try_search.py "모터가 너무 뜨거워요"
    python eval\\try_search.py "벨트가 한쪽으로 쏠려요" --model L400
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.getcwd())  # 레포 폴더에서 실행해야 chatbot 패키지를 찾음

from chatbot.core.config import Settings  # noqa: E402
from chatbot.pipeline import RagPipeline  # noqa: E402
from chatbot.rag.glossary import Glossary  # noqa: E402
from chatbot.rag.llm import LLMClient  # noqa: E402
from chatbot.rag.retriever import CANDIDATE_K, TOP_K, Retriever  # noqa: E402


def show(title, hits):
    print(f"\n[{title}]")
    for rank, hit in enumerate(hits[:TOP_K], start=1):
        kind = hit.metadata.get("kb_type") or hit.metadata.get("chunk_type")
        print(f"  {rank}. {hit.score:8.4f}  {hit.id}  ({hit.doc_id} p.{hit.page}, {kind})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--model", help="설비 모델명 (models.csv의 model_name, 예: 138-ACC · L400)")
    args = parser.parse_args()

    settings = Settings.load()
    llm = LLMClient(settings)
    retriever = Retriever.from_settings(settings, llm)
    pipeline = RagPipeline(llm)
    glossary = Glossary().load_glossary(os.path.join("data", "glossary.csv"))

    info = pipeline.understand_question(args.question, {})
    queries = pipeline.rewrite_query(args.question, glossary)
    equipment = None
    if args.model:
        models = Glossary().load_models(os.path.join("data", "models.csv"))
        model = models[args.model]
        equipment = {"model_name": model.model_name, "aliases": model.aliases, "doc_ids": model.doc_ids}

    print(f"질문: {args.question}")
    print(f"질문 유형: {info.qtype} / 설비: {args.model or '없음'}")
    print(f"영문 검색어: {queries}")

    lists = []
    for query in queries:
        bm25 = retriever.bm25_search(query, CANDIDATE_K, equipment)
        vector = retriever.vector_search(query, CANDIDATE_K, equipment)
        show(f"BM25 · {query}", bm25)
        show(f"벡터 · {query}", vector)
        lists += [bm25, vector]
    show("RRF(k=60) 결합", retriever.rrf_merge(lists))

    started = time.perf_counter()
    hits, filter_released = retriever.search(queries, equipment, info.qtype)
    ms = (time.perf_counter() - started) * 1000
    show(f"리랭킹 뒤 최종 (검색 {ms:.0f} ms, 필터 해제 {filter_released})", hits)
    print(f"\n1위 점수 {hits[0].score:.4f} / 기준값 {retriever.threshold} → "
          f"{'확인되지 않음' if retriever.is_not_found(hits) else '답변 가능'}" if hits else "\n결과 없음")


if __name__ == "__main__":
    main()
