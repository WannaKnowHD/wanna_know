"""실행 파일: python app.py → Gradio 앱 시작."""
import traceback

from chatbot.core.auth import AuthManager
from chatbot.core.config import Settings
from chatbot.core.state import ConversationState
from chatbot.db.db import Database
from chatbot.ui.dashboard import AdminDashboard, load_kb_symptoms
from chatbot.ui.gradio_app import ChatApp

def build_pipeline(settings: Settings, db: Database):
    """질문 처리 파이프라인을 앱 시작 때 1번 만든다 (색인·리랭커·KB·안전 규칙을 여기서 읽음).

    색인이 없는 등 만들지 못하면 None 을 돌려준다. 그러면 로그인·관리자 화면은 그대로 쓰고, 질문에는 "사용할 수 없음" 안내가 나온다.
    """
    try:
        # 무거운 모듈은 여기서 불러온다 (테스트가 app.py 를 불러올 때 느려지지 않게)
        from chatbot.core.logger import RequestLogger
        from chatbot.core.safety import SafetyRules
        from chatbot.pipeline import RagPipeline, load_kb
        from chatbot.rag.glossary import Glossary
        from chatbot.rag.llm import LLMClient
        from chatbot.rag.retriever import Retriever

        llm = LLMClient(settings)
        retriever = Retriever.from_settings(settings, llm)
        print("[app] 리랭커 모델을 미리 불러오는 중입니다 (처음 질문이 느려지지 않게)...")
        retriever.warm_up()
        return RagPipeline(
            llm,
            retriever=retriever,
            safety=SafetyRules(),
            request_logger=RequestLogger(db),
            glossary=Glossary().load_glossary("data/glossary.csv"),
            kb=load_kb(settings.kb_dir),
        )
    except Exception as e:  # noqa: BLE001 — 색인 없음·키 누락 등. 화면은 계속 띄우고 원인만 알림
        print(f"[app] 질문 처리 파이프라인을 만들지 못했습니다 ({type(e).__name__}: {e}). 질문에는 안내 문구만 나옵니다.")
        traceback.print_exc()   # 어느 줄에서 실패했는지 (색인 없음·키 누락·KB 오류 등)
        return None


if __name__ == "__main__":
    settings = Settings.load()
    db = Database().connect(settings)   # 접속 정보가 틀렸으면 여기서 바로 멈춤
    # 어느 DB 에 붙었는지 보여 준다. 접속 대상은 .env 의 DB_HOST·DB_PORT·DB_NAME 으로만 정해진다(코드에는 없음).
    #   지금은 DB_NAME=test (연습용 DB). 실제 DB 로 바꿀 때는 .env 의 DB_NAME 만 고치면 되고 이 파일은 그대로 둔다.
    print(f"[app] DB: {settings.db_host}:{settings.db_port}/{settings.db_name}")
    if settings.db_name == "test":
        print("[app] 주의: 연습용 test DB 에 붙었습니다. 실제 DB 로 바꾸려면 .env 의 DB_NAME 을 바꾸세요.")
    app = ChatApp(
        settings=settings,
        auth=AuthManager(db, session_hours=settings.session_hours),
        conversation=ConversationState(db),
        dashboard=AdminDashboard(db, load_kb_symptoms(settings.kb_dir)),
        pipeline=build_pipeline(settings, db),
    )
    try:
        app.build_ui().launch(**app.launch_options())
    finally:
        db.close()
