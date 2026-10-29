"""로그인 뒤 채팅 화면. ChatApp(gradio_app.py)이 여기서 가져다 조립한다.

  chat_screen.py  화면 전체 = 사이드바 + 채팅 영역    (chat.css: 색 변수·전체 배치)
  sidebar.py      대화 기록창·사용자 정보·로그아웃    (sidebar.css)
  chat_area.py    상단 바·채팅창·예시 질문·입력 바    (chat_area.css)
  messages.py     말풍선·답변 카드 HTML              (messages.css)
  fake_answer.py  RagPipeline 연결 전 가짜 응답

관리자 대시보드는 ui/admin, 로그인 화면은 ui/login.
"""
from chatbot.ui.chat.chat_area import CHAT_AREA_CSS_PATH, EXAMPLE_QUESTIONS, ChatArea, build_chat_area
from chatbot.ui.chat.chat_screen import CHAT_CSS_PATH, ChatScreen, build_chat_screen
from chatbot.ui.chat.fake_answer import fake_answer, fake_photo_answer
from chatbot.ui.chat.messages import (
    MESSAGES_CSS_PATH,
    render_answer_card,
    render_equipment,
    render_user_message,
    render_welcome,
)
from chatbot.ui.chat.sidebar import (
    SIDEBAR_CSS_PATH,
    Sidebar,
    build_sidebar,
    history_key,
    render_history,
    render_user_info,
)

# launch(css_paths=...) 에 넘기는 스타일 파일. 색 변수를 정의한 chat.css 가 먼저 와야 한다.
CHAT_CSS_PATHS = [CHAT_CSS_PATH, SIDEBAR_CSS_PATH, CHAT_AREA_CSS_PATH, MESSAGES_CSS_PATH]

__all__ = [
    "CHAT_CSS_PATHS", "EXAMPLE_QUESTIONS",
    "ChatScreen", "build_chat_screen", "ChatArea", "build_chat_area", "Sidebar", "build_sidebar",
    "history_key", "render_history", "render_user_info",
    "render_answer_card", "render_equipment", "render_user_message", "render_welcome",
    "fake_answer", "fake_photo_answer",
]
