"""라이트/다크 전환 (스타일: theme.css, 채팅 화면 색은 ui/chat/chat.css 의 body.dark)

다크 = Figma Design 섹션의 "B. 추천 다크 모드 (딥 포레스트)". 로그인 · 채팅 · 관리자 화면을 모두 맞춰서 전환을 켬 (10/30).
처음엔 라이트, 전환 버튼으로 고른 값은 브라우저에 기억함. 다시 라이트로 고정하려면 THEME_TOGGLE_ENABLED 를 False 로 바꾸면 된다.
"""
from __future__ import annotations

from pathlib import Path

THEME_CSS_PATH = Path(__file__).parent / "theme.css"

THEME_TOGGLE_ENABLED = True

# 다크는 <body class="dark"> 로 켜고, 고른 값을 브라우저(localStorage)에 기억함. theme.css 가 .dark 기준으로 색을 바꿈
THEME_KEY = "wk-theme"

# 끈 상태: Gradio 가 브라우저 설정(OS 다크 모드)을 따라 dark 를 붙여도 계속 떼어 내 라이트로 고정
_FORCE_LIGHT_JS = (
    "() => { const keep = () => document.body.classList.remove('dark'); keep();"
    " new MutationObserver(keep).observe(document.body, {attributes: true, attributeFilter: ['class']}); }"
)
# 켠 상태: 고른 값(없으면 라이트)을 window.wkTheme 에 두고, Gradio 가 OS 설정을 따라 dark 를 붙이거나 떼도 그 값으로 되돌림
_KEEP_CHOICE_JS = (
    "() => { let dark = false; try { dark = localStorage.getItem('%s') === 'dark'; } catch (e) {}"
    " window.wkTheme = dark ? 'dark' : 'light';"
    " const keep = () => { const on = window.wkTheme === 'dark';"
    " if (document.body.classList.contains('dark') !== on) document.body.classList.toggle('dark', on); }; keep();"
    " new MutationObserver(keep).observe(document.body, {attributes: true, attributeFilter: ['class']}); }" % THEME_KEY
)

# 페이지를 열 때 실행됨. app.py 가 launch(js=...) 로 넘김 (ChatApp.launch_options)
THEME_INIT_JS = _KEEP_CHOICE_JS if THEME_TOGGLE_ENABLED else _FORCE_LIGHT_JS
THEME_TOGGLE_JS = (
    "() => { window.wkTheme = document.body.classList.contains('dark') ? 'light' : 'dark';"
    " document.body.classList.toggle('dark', window.wkTheme === 'dark');"
    " try { localStorage.setItem('%s', window.wkTheme); } catch (e) {} }" % THEME_KEY
)


def bind_theme_toggle(*buttons) -> None:
    """전환 버튼을 눌렀을 때 서버를 거치지 않고 브라우저에서 바로 라이트/다크를 바꾸게 연결한다. 꺼 두었으면 아무것도 안 함."""
    if not THEME_TOGGLE_ENABLED:
        return
    for button in buttons:
        button.click(fn=None, js=THEME_TOGGLE_JS)
