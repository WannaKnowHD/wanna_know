"""라이트/다크 전환 (스타일: theme.css, 채팅 화면 색은 ui/chat/chat.css 의 body.dark)

지금은 화면별 다크 모드를 하나씩 구현하는 중이라 전환을 꺼 두고 라이트로 고정한다.
다시 켜려면 THEME_TOGGLE_ENABLED 를 True 로 바꾸면 된다 (전환 버튼 표시 + 고른 값 기억 + 다크 복원).
"""
from __future__ import annotations

from pathlib import Path

THEME_CSS_PATH = Path(__file__).parent / "theme.css"

THEME_TOGGLE_ENABLED = False

# 다크는 <body class="dark"> 로 켜고, 고른 값을 브라우저(localStorage)에 기억함. theme.css 가 .dark 기준으로 색을 바꿈
THEME_KEY = "wk-theme"

# 끈 상태: Gradio 가 브라우저 설정(OS 다크 모드)을 따라 dark 를 붙여도 계속 떼어 내 라이트로 고정
_FORCE_LIGHT_JS = (
    "() => { const keep = () => document.body.classList.remove('dark'); keep();"
    " new MutationObserver(keep).observe(document.body, {attributes: true, attributeFilter: ['class']}); }"
)
# 켠 상태: 저장된 값으로 되돌림
_RESTORE_JS = (
    "() => { try { if (localStorage.getItem('%s') === 'dark') document.body.classList.add('dark'); } catch (e) {} }" % THEME_KEY
)

# 페이지를 열 때 실행됨. app.py 가 launch(js=...) 로 넘김 (ChatApp.launch_options)
THEME_INIT_JS = _RESTORE_JS if THEME_TOGGLE_ENABLED else _FORCE_LIGHT_JS
THEME_TOGGLE_JS = (
    "() => { const dark = document.body.classList.toggle('dark');"
    " try { localStorage.setItem('%s', dark ? 'dark' : 'light'); } catch (e) {} }" % THEME_KEY
)


def bind_theme_toggle(*buttons) -> None:
    """전환 버튼을 눌렀을 때 서버를 거치지 않고 브라우저에서 바로 라이트/다크를 바꾸게 연결한다. 꺼 두었으면 아무것도 안 함."""
    if not THEME_TOGGLE_ENABLED:
        return
    for button in buttons:
        button.click(fn=None, js=THEME_TOGGLE_JS)
