"""로그인 화면과 로그인 뒤 채팅 화면 틀. ChatApp(gradio_app.py)이 여기서 가져다 조립한다.

  theme.py       라이트/다크 전환       (theme.css)
  login_form.py  로그인 화면            (login_form.css)
  chat_shell.py  채팅 화면 틀           (chat_shell.css)
"""
from chatbot.ui.login.chat_shell import CHAT_SHELL_CSS_PATH, ChatShell, build_chat_shell, render_history, render_user_info
from chatbot.ui.login.login_form import (
    LOGIN_FORM_CSS_PATH,
    MSG_EMPTY,
    MSG_INVALID,
    MSG_SERVER,
    LoginForm,
    build_login_form,
    render_login_error,
)
from chatbot.ui.login.theme import THEME_CSS_PATH, THEME_INIT_JS, bind_theme_toggle

# launch(css_paths=...) 에 넘기는 스타일 파일. 변수를 정의한 theme.css 가 먼저 와야 한다.
LOGIN_CSS_PATHS = [THEME_CSS_PATH, LOGIN_FORM_CSS_PATH, CHAT_SHELL_CSS_PATH]

__all__ = [
    "LOGIN_CSS_PATHS", "THEME_INIT_JS", "bind_theme_toggle",
    "MSG_EMPTY", "MSG_INVALID", "MSG_SERVER", "LoginForm", "build_login_form", "render_login_error",
    "ChatShell", "build_chat_shell", "render_history", "render_user_info",
]
