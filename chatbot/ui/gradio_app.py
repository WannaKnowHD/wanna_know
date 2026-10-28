"""로그인·채팅 화면 (화면 요소·스타일은 ui/login, 관리자 화면은 ui/dashboard)"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from chatbot.core.state import ConversationState
from chatbot.ui.dashboard import ADMIN_CSS_PATH, AdminDashboard
from chatbot.ui.login import (
    LOGIN_CSS_PATHS,
    MSG_EMPTY,
    MSG_INVALID,
    MSG_SERVER,
    THEME_INIT_JS,
    bind_theme_toggle,
    build_chat_shell,
    build_login_form,
    render_history,
    render_login_error,
    render_user_info,
)

if TYPE_CHECKING:
    from chatbot.core.auth import AuthManager
    from chatbot.core.config import Settings


class ChatApp:
    """ChatApp (기능명세서 기준 뼈대)."""

    def __init__(
        self,
        settings: Settings | None = None,
        auth: AuthManager | None = None,
        conversation: ConversationState | None = None,
    ):
        """
        받는 것:
          settings     : 세션 저장 키·암호 키(BROWSER_STORAGE_KEY, BROWSER_SECRET)를 읽음
          auth         : 로그인·세션 (core.auth.AuthManager)
          conversation : 사용자별 대화 상태 (core.state.ConversationState). 없으면 DB 없이 화면 상태만 다룸
        """
        self._settings = settings
        self._auth = auth
        self._conversation = conversation or ConversationState()
        self._dashboard = AdminDashboard()

    @staticmethod
    def launch_options() -> dict:
        """app.py 가 launch(**ChatApp.launch_options()) 로 넘기는 값 (스타일 파일과 다크 모드 복원 스크립트)"""
        return {"css_paths": [ADMIN_CSS_PATH, *LOGIN_CSS_PATHS], "js": THEME_INIT_JS}

    def _screen(self, state: dict):
        """state 에 맞는 화면 상태를 만든다 (로그인 화면 / 채팅 화면 / 관리자 대시보드 중 하나만 보임).

        Returns: (로그인 화면, 채팅 화면, 관리자 화면, 관리자 전환 버튼, 채팅 사원 정보, 관리자 사원 정보, 대화 기록, 로그인 안내)
        """
        user = state.get("user")
        admin_mode = bool(state.get("admin_mode")) and user is not None and user.is_admin
        history = render_history(self.show_history(state)) if user is not None else ""
        return (
            gr.update(visible=user is None),
            gr.update(visible=user is not None and not admin_mode),
            gr.update(visible=admin_mode),
            gr.update(visible=user is not None and user.is_admin),
            render_user_info(user),
            AdminDashboard.render_user_info(user),
            history,
            render_login_error(state.get("login_error")),
        )

    def build_ui(self):
        """
        로그인 화면(사원번호·비밀번호 입력창, 로그인 버튼), 채팅 화면(채팅창, 텍스트 입력창, 사진 첨부, 예시 질문 버튼, 대화 기록, 대화 초기화,
        관리자 전환 버튼, 현재 설비 표시, 로그아웃 버튼)과 관리자 대시보드 탭(운영 현황·답변 품질·시스템·KB 현황)을 구성. 로그인 전에는 로그인
        화면만 표시하고, 관리자 전환 버튼은 관리자 계정에게만 표시. 세션 토큰은 gr.BrowserState(고정 저장 키·암호 키)에, 로그인한
        사원(User)은 gr.State에 보관

        Returns: gr.Blocks
        """
        storage_key = self._settings.browser_storage_key if self._settings else None
        secret = self._settings.browser_secret if self._settings else None

        with gr.Blocks(title="Wanna Know") as demo:
            token_state = gr.BrowserState(None, storage_key=storage_key, secret=secret)   # 세션 토큰 (브라우저)
            state = gr.State({})                                                            # 로그인한 사원 등 (서버)

            login = build_login_form()      # ui/login/login_form.py
            chat = build_chat_shell()       # ui/login/chat_shell.py

            # ---- 관리자 대시보드 ----
            admin_view = self._dashboard.build_admin_tab()
            admin_view.visible = False
            admin_user_info = self._dashboard.user_info_display

            screen = [login.root, chat.root, admin_view, chat.admin_button, chat.user_info, admin_user_info, chat.history, login.error]

            def load_event(token):
                _, new_state = self.on_load(token)
                return (new_state, *self._screen(new_state))

            def login_event(employee_id, password, current):
                try:
                    token, new_state = self.on_login(employee_id, password, current)
                except Exception as e:  # DB 연결 문제 등: 로그인 화면에 머물고 안내만 보여 줌 (입력은 그대로)
                    print(f"[gradio_app] 로그인 처리 실패: {type(e).__name__}: {e}")
                    new_state = dict(current or {})
                    new_state["login_error"] = MSG_SERVER
                    token = None
                # 실패하면 token 이 None → 브라우저에 남아 있는 값은 건드리지 않음
                return (token if token else gr.skip(), new_state, *self._screen(new_state))

            def logout_event(token, current):
                _, new_state = self.on_logout(token, current)
                return (None, new_state, *self._screen(new_state), "", "")

            def admin_event(current):
                _, new_state = self.change_admin_mode(current)
                return (new_state, *self._screen(new_state))

            def back_event(current):  # 뒤로가기: 관리자 모드는 유지한 채 채팅 화면만 보여 줌
                new_state = dict(current or {})
                return (new_state, *self._screen({**new_state, "admin_mode": False}))

            demo.load(load_event, inputs=[token_state], outputs=[state, *screen])
            login_inputs = [login.employee, login.password, state]
            login_outputs = [token_state, state, *screen]
            login.button.click(login_event, inputs=login_inputs, outputs=login_outputs)
            login.password.submit(login_event, inputs=login_inputs, outputs=login_outputs)
            login.employee.submit(login_event, inputs=login_inputs, outputs=login_outputs)
            logout_outputs = [token_state, state, *screen, login.employee, login.password]
            chat.logout_button.click(logout_event, inputs=[token_state, state], outputs=logout_outputs)
            self._dashboard.logout_button.click(logout_event, inputs=[token_state, state], outputs=logout_outputs)
            chat.admin_button.click(admin_event, inputs=[state], outputs=[state, *screen])
            self._dashboard.back_button.click(back_event, inputs=[state], outputs=[state, *screen])
            bind_theme_toggle(login.theme_button, chat.theme_button)

        return demo

    def on_load(self, token: str | None):
        """
        페이지를 열거나 새로고침할 때 브라우저에 저장된 토큰으로 AuthManager.restore_session 호출. 유효하면 사원 정보를 대화 상태에
        넣고 채팅 화면 표시(관리자면 관리자 전환 버튼도 표시), 아니면 로그인 화면 표시

        Returns: User | None, dict
        """
        state: dict = {}
        user = self._auth.restore_session(token) if self._auth is not None and token else None
        if user is not None:
            # ConversationState 는 받은 dict 를 고치지 않고 새 dict 를 돌려주므로 반환값을 받아 쓴다. 권한은 restore_session 이 DB 에서 다시 읽은 값
            state = self._conversation.set_user(state, user)
        return user, state

    def on_login(self, employee_id: str, password: str, state: dict):
        """
        로그인 화면에서 입력한 사원번호·비밀번호를 확인. 미입력 → 입력 요청 안내, 불일치 → "사원번호 또는 비밀번호가 올바르지 않습니다" 안내 후
        재입력, 일치 → AuthManager.create_session으로 토큰을 발급해 브라우저(BrowserState)에 저장하고, 사원 정보를 대화
        상태에 저장한 뒤 채팅 화면으로 이동 (관리자 계정이면 관리자 전환 버튼 표시)

        Returns: str (token), dict
        """
        state = dict(state or {})
        state.pop("login_error", None)
        employee_id = (employee_id or "").strip()
        if not employee_id or not password:
            state["login_error"] = MSG_EMPTY          # 미입력 → 입력 요청 안내
            return None, state
        user = self._auth.authenticate(employee_id, password)
        if user is None:
            state["login_error"] = MSG_INVALID        # 사원번호 없음·비밀번호 불일치는 같은 문구 (어느 쪽이 틀렸는지 알려 주지 않음)
            return None, state
        token = self._auth.create_session(user)
        state = self._conversation.set_user(state, user)   # 새 dict 를 돌려줌 (반환값을 써야 사원 정보가 담김)
        return token, state

    def on_logout(self, token: str, state: dict):
        """
        로그아웃 버튼 처리: AuthManager.delete_session으로 DB의 세션을 지우고 브라우저의 토큰을 비움.
        ConversationState.clear_user로 사원 정보·관리자 모드·설비 필터와 화면의 대화 내용을 비운 뒤 로그인 화면으로 전환. DB에
        저장된 대화 기록은 지우지 않음

        Returns: bool, dict
        """
        try:
            self._auth.delete_session(token)
        except Exception as e:  # DB 오류여도 이 화면은 로그아웃함. 남은 세션은 만료 시각(8시간)에 정리됨
            print(f"[gradio_app] 세션 삭제 실패: {type(e).__name__}: {e}")
        state = self._conversation.clear_user(state)   # 새 dict 를 돌려줌 (사원 정보·관리자 모드·설비 필터 모두 비움)
        return True, state

    def on_submit(self, message: str, history: list, state: dict):
        """
        텍스트 질문을 받아 RagPipeline.answer를 호출하고 답변을 채팅창에 표시. 근거 부족이면 "확인되지 않음" 안내와 함께 설비·증상을
        구체적으로 다시 질문하도록 안내. 질문은 매번 독립적으로 처리 (이전 대화 맥락을 쓰지 않음)

        Returns: list, dict
        """
        raise NotImplementedError("ChatApp.on_submit")

    def on_image(self, image: bytes, history: list, state: dict):
        """
        업로드한 사진을 입력 판별 후 명판이면 설비 설정(설비 표시), 손상 사진이면 답변 생성, 그 외·확신도 미달이면 재촬영 안내

        Returns: list, dict
        """
        raise NotImplementedError("ChatApp.on_image")

    def on_follow_up(self, question: str, history: list, state: dict):
        """
        답변 아래 후속 질문 버튼 클릭을 받아 그 문장을 새 질문으로 처리. 후속 질문은 설비·부품이 들어간 완전한 문장이라 이전 대화 맥락이 필요 없음

        Returns: list, dict
        """
        raise NotImplementedError("ChatApp.on_follow_up")

    def show_examples(self, template: str | None):
        """
        예시 질문 템플릿 목록을 출력하고, 클릭한 템플릿을 입력창에 추가

        Returns: list[str], str
        """
        raise NotImplementedError("ChatApp.show_examples")

    def show_history(self, state: dict):
        """
        로그인한 사원의 과거 대화 기록을 ConversationState.load_history로 MySQL에서 불러와 목록(로그창)으로 출력

        Returns: list
        """
        user = state.get("user")
        return self._conversation.load_history(user.employee_id) if user is not None else []

    def reset_conversation(self, state: dict):
        """
        대화 초기화: 설비 필터를 비우고 화면의 대화 내용을 지워 전체 매뉴얼 검색으로 되돌림

        Returns: dict
        """
        raise NotImplementedError("ChatApp.reset_conversation")

    def change_admin_mode(self, state: dict):
        """
        관리자 전환 버튼(관리자 계정에게만 표시) 처리: 대화 상태의 사원 권한이 관리자인지 다시 확인하고 관리자 대시보드로 전환

        Returns: bool, dict
        """
        user = state.get("user")
        if user is None or not user.is_admin:   # 버튼은 관리자에게만 보이지만, 여기서도 권한을 다시 확인
            return False, state
        state["admin_mode"] = True
        return True, state

    def exit_admin_mode(self, state: dict):
        """
        관리자 모드 해제 후 채팅 화면으로 복귀 (뒤로가기는 모드를 유지한 채 채팅 화면으로 이동)

        Returns: dict
        """
        state["admin_mode"] = False
        return state

    def render_answer(self, answer: dict):
        """
        응답 JSON을 화면용 텍스트로 변환 (요약 → 안전 문구 → 원인·조치 → 출처·원문 → 그림 순서, 후속 질문 버튼 포함), 원문 보기에서
        근거가 표·그림이면 잘라 둔 이미지를 같이 표시

        Returns: str
        """
        raise NotImplementedError("ChatApp.render_answer")
