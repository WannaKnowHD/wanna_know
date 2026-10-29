"""실행 파일: python app.py → Gradio 앱 시작."""
from chatbot.core.auth import AuthManager
from chatbot.core.config import Settings
from chatbot.core.state import ConversationState
from chatbot.db.db import Database
from chatbot.ui.dashboard import AdminDashboard, load_kb_symptoms
from chatbot.ui.gradio_app import ChatApp

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
    )
    try:
        app.build_ui().launch(**app.launch_options())
    finally:
        db.close()
