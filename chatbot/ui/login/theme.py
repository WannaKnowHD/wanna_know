"""라이트/다크 전환 (스타일: theme.css)"""
from __future__ import annotations

from pathlib import Path

THEME_CSS_PATH = Path(__file__).parent / "theme.css"

# 다크는 <body class="dark"> 로 켜고, 고른 값을 브라우저(localStorage)에 기억함. theme.css 가 .dark 기준으로 색을 바꿈
THEME_KEY = "wk-theme"

# 페이지를 열 때 저장된 값으로 되돌림. app.py 가 launch(js=...) 로 넘김 (ChatApp.launch_options)
THEME_INIT_JS = (
    "() => { try { if (localStorage.getItem('%s') === 'dark') document.body.classList.add('dark'); } catch (e) {} }" % THEME_KEY
)
THEME_TOGGLE_JS = (
    "() => { const dark = document.body.classList.toggle('dark');"
    " try { localStorage.setItem('%s', dark ? 'dark' : 'light'); } catch (e) {} }" % THEME_KEY
)


def bind_theme_toggle(*buttons) -> None:
    """전환 버튼을 눌렀을 때 서버를 거치지 않고 브라우저에서 바로 라이트/다크를 바꾸게 연결한다."""
    for button in buttons:
        button.click(fn=None, js=THEME_TOGGLE_JS)
