"""로그인·채팅 화면을 조립하고 이벤트를 연결 (화면 요소·스타일: 로그인 ui/login, 채팅 ui/chat, 관리자 ui/dashboard·ui/admin)"""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr
from pydantic import ValidationError

from chatbot.core.schemas import AnswerResponse, DamageResult, Equipment, ImageResult, NameplateResult
from chatbot.core.state import ConversationState
from chatbot.ui.chat import (
    CHAT_CSS_PATHS,
    EXAMPLE_QUESTIONS,
    build_chat_screen,
    history_key,
    render_answer_card,
    render_typing,
    render_history,
    render_user_info,
    render_user_message,
)
from chatbot.ui.dashboard import ADMIN_CSS_PATH, AdminDashboard
from chatbot.ui.login import (
    LOGIN_CSS_PATHS,
    MSG_EMPTY,
    MSG_INVALID,
    MSG_SERVER,
    THEME_INIT_JS,
    bind_theme_toggle,
    build_login_form,
    render_login_error,
)

if TYPE_CHECKING:
    from chatbot.core.auth import AuthManager
    from chatbot.core.config import Settings
    from chatbot.pipeline import RagPipeline

HIDE = {"show_progress": "hidden"}   # 이벤트 처리 중 Gradio 기본 표시(주황 로딩 애니메이션 · processing 시간) 끄기
MSG_BAD_ANSWER = "답변 형식이 올바르지 않아 표시할 수 없습니다. 다시 질문해 주세요."   # 응답이 AnswerResponse 형식에 맞지 않을 때
MSG_NO_PIPELINE = "답변 기능을 아직 사용할 수 없습니다. 관리자에게 문의해 주세요."     # RagPipeline 을 만들지 못했을 때 (색인 없음 등)
MSG_ANSWER_ERROR = "답변을 만드는 중 문제가 생겼어요. 잠시 후 다시 질문해 주세요."     # RagPipeline.answer 밖에서 예외가 났을 때

# ---- 사진 첨부 (on_image, WBS 3.7.5) ----
# 사진 확신도는 재촬영 판단에 쓰지 않고 응답(image.confidence)·로그에 기록만 한다 (vision.py NameplateReader 주석 참고)
MSG_PHOTO_OTHER = "명판이나 손상 부위를 가까이 찍어 주세요."          # 기능명세서 I-01 문구
MSG_PHOTO_ERROR = "사진을 분석하지 못했습니다. 잠시 후 다시 시도해 주세요."
MSG_NAMEPLATE_RETAKE = "명판 글자가 잘 보이지 않습니다. 명판을 정면에서 가까이 다시 찍어 주세요."
MSG_DAMAGE_RETAKE = "손상 부위가 잘 보이지 않습니다. 손상된 곳을 가까이, 밝은 곳에서 다시 찍어 주세요."
PHOTO_QUESTIONS = {   # 대화 기록(로그창)에 남길 질문 칸 (사진은 글자가 없으므로)
    "nameplate_photo": "(사진) 명판",
    "damage_photo": "(사진) 손상 부위",
    "other_photo": "(사진)",
}


class ChatApp:
    """ChatApp (기능명세서 기준 뼈대)."""

    def __init__(
        self,
        settings: Settings | None = None,
        auth: AuthManager | None = None,
        conversation: ConversationState | None = None,
        dashboard: AdminDashboard | None = None,
        pipeline: RagPipeline | None = None,
    ):
        """
        받는 것:
          settings     : 세션 저장 키·암호 키(BROWSER_STORAGE_KEY, BROWSER_SECRET)를 읽음
          auth         : 로그인·세션 (core.auth.AuthManager)
          conversation : 사용자별 대화 상태 (core.state.ConversationState). 없으면 DB 없이 화면 상태만 다룸
          dashboard    : 관리자 대시보드 (ui.dashboard.AdminDashboard). 없으면 DB 없이 화면 틀만 만듦
          pipeline     : 질문 처리 (pipeline.RagPipeline, 앱 시작 때 1번 만든 것). 없으면 질문마다 "사용할 수 없음" 안내 카드를 보여 줌
        """
        self._settings = settings
        self._auth = auth
        self._conversation = conversation or ConversationState()
        self._dashboard = dashboard or AdminDashboard()
        self._pipeline = pipeline

    @staticmethod
    def launch_options() -> dict:
        """app.py 가 launch(**ChatApp.launch_options()) 로 넘기는 값 (스타일 파일과 다크 모드 복원 스크립트)"""
        return {"css_paths": [ADMIN_CSS_PATH, *LOGIN_CSS_PATHS, *CHAT_CSS_PATHS], "js": THEME_INIT_JS}

    def _screen(self, state: dict):
        """state 에 맞는 화면 상태를 만든다 (로그인 화면 / 채팅 화면 / 관리자 대시보드 중 하나만 보임).

        Returns: (로그인 화면, 채팅 화면, 관리자 화면, 관리자 전환 버튼, 사이드바 사원 정보, 관리자 사원 정보, 대화 기록, 로그인 안내)
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
            chat = build_chat_screen()      # ui/chat/chat_screen.py (사이드바 + 채팅 영역)
            sidebar, area = chat.sidebar, chat.area

            # ---- 관리자 대시보드 ----
            admin_view = self._dashboard.build_admin_tab(state)   # 새로고침 · 검토 저장 때 관리자인지 다시 확인
            admin_view.visible = False
            admin_user_info = self._dashboard.user_info_display

            screen = [
                login.root, chat.root, admin_view, area.admin_button, sidebar.user_info, admin_user_info,
                sidebar.history, login.error,
            ]
            # 채팅창을 처음 상태(빈 채팅창 + 예시 질문)로 되돌릴 때 함께 바꾸는 요소
            pending_image = gr.State(None)   # 첨부했지만 아직 보내지 않은 사진 경로 (미리보기 중)
            preview_outputs = [pending_image, area.preview_row, area.preview]
            clear_preview = (None, gr.update(visible=False), None)
            fresh_chat = [area.chatbot, area.examples, area.message, *preview_outputs]
            fresh_values = ([], gr.update(visible=True), "", *clear_preview)

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
                return (None, new_state, *self._screen(new_state), "", "", *fresh_values)

            def admin_event(current):
                _, new_state = self.change_admin_mode(current)
                return (new_state, *self._screen(new_state))

            def back_event(current):  # 뒤로가기: 관리자 모드는 유지한 채 채팅 화면만 보여 줌
                new_state = dict(current or {})
                return (new_state, *self._screen({**new_state, "admin_mode": False}))

            # 처리 중 표시(Gradio 주황 로딩 · processing 시간)는 모든 이벤트에서 숨김 (HIDE). 답변을 기다릴 때는 render_typing 말풍선
            demo.load(load_event, inputs=[token_state], outputs=[state, *screen], **HIDE)
            login_inputs = [login.employee, login.password, state]
            login_outputs = [token_state, state, *screen]
            login.button.click(login_event, inputs=login_inputs, outputs=login_outputs, **HIDE)
            login.password.submit(login_event, inputs=login_inputs, outputs=login_outputs, **HIDE)
            login.employee.submit(login_event, inputs=login_inputs, outputs=login_outputs, **HIDE)
            logout_outputs = [token_state, state, *screen, login.employee, login.password, *fresh_chat]
            sidebar.logout_button.click(logout_event, inputs=[token_state, state], outputs=logout_outputs, **HIDE)
            self._dashboard.logout_button.click(logout_event, inputs=[token_state, state], outputs=logout_outputs, **HIDE)
            area.admin_button.click(admin_event, inputs=[state], outputs=[state, *screen], **HIDE).then(
                self._dashboard.refresh, inputs=self._dashboard.refresh_inputs, outputs=self._dashboard.refresh_outputs, **HIDE
            )   # 대시보드로 넘어가면 기본 기간(최근 7일) 값을 채움
            self._dashboard.back_button.click(back_event, inputs=[state], outputs=[state, *screen], **HIDE)
            bind_theme_toggle(login.theme_button, area.theme_button)

            # ---- 채팅: 질문 보내기 · 사진 첨부(미리보기) · 예시 질문 · 대화 초기화 ----
            # 질문 말풍선을 먼저 보여 주고(show_question_event), 이어서 답변을 만들어 붙인다(answer_event)
            def show_question_event(message, history, current, image):
                history = list(history or [])
                message = (message or "").strip()
                if (current or {}).get("user") is None or not (message or image):   # 로그인 전·빈 입력은 처리하지 않음
                    return history, None, gr.update(), gr.update(), *clear_preview
                pending = {"message": message, "image": image, "history": history}   # 답변 단계에서 이 기록에 질문+답변을 붙임
                shown = list(history)
                if image:
                    shown.append({"role": "user", "content": {"path": image}})
                if message:
                    shown.append({"role": "user", "content": render_user_message(message)})
                shown.append({"role": "assistant", "content": render_typing()})   # 답변 생성 중 말풍선 (Figma 01-1). 답변이 오면 바뀜
                return shown, pending, "", gr.update(visible=False), *clear_preview

            def answer_event(pending, history, current):
                if pending is None:
                    return history, current, gr.update(visible=not history), gr.update()
                if pending["image"]:
                    # 사진 말풍선은 이미 보였으므로 on_image(3.7.5)에는 말풍선까지 붙인 기록을 넘겨 답변 카드만 붙게 함
                    shown = list(pending["history"]) + [{"role": "user", "content": {"path": pending["image"]}}]
                    if pending["message"]:
                        shown.append({"role": "user", "content": render_user_message(pending["message"])})
                    history, new_state = self.on_image(Path(pending["image"]).read_bytes(), shown, current)
                else:
                    history, new_state = self.on_submit(pending["message"], pending["history"], current)
                # 방금 저장한 대화가 사이드바 대화 기록 맨 위에 보이게 다시 그림
                return history, new_state, gr.update(visible=not history), render_history(self.show_history(new_state))

            def attach_event(file):
                return file, gr.update(visible=bool(file)), file

            def remove_event():
                return None, gr.update(visible=False), None

            def history_event(current, evt: gr.EventData):   # 대화 기록 항목을 누르면 그 대화를 채팅창에 보여 줌 (C-03)
                data = evt._data if isinstance(evt._data, dict) else {}
                messages = self.open_history(current, data.get("index"), data.get("key"))
                if messages is None:   # 그사이 기록이 바뀌어 찾지 못하면 채팅창은 그대로
                    return gr.update(), gr.update()
                return messages, gr.update(visible=False)

            def reset_event(current):
                new_state = self.reset_conversation(current)
                return (new_state, *fresh_values)

            pending_question = gr.State(None)   # 질문은 보였지만 아직 답변이 붙지 않은 요청
            question_outputs = [area.chatbot, pending_question, area.message, area.examples, *preview_outputs]
            question_inputs = [area.message, area.chatbot, state, pending_image]
            answer_inputs = [pending_question, area.chatbot, state]
            answer_outputs = [area.chatbot, state, area.examples, sidebar.history]
            for trigger in (area.send_button.click, area.message.submit):
                trigger(show_question_event, inputs=question_inputs, outputs=question_outputs, **HIDE).then(
                    answer_event, inputs=answer_inputs, outputs=answer_outputs, **HIDE
                )
            area.image_button.upload(attach_event, inputs=[area.image_button], outputs=preview_outputs, **HIDE)
            area.preview_remove.click(remove_event, outputs=preview_outputs, **HIDE)
            sidebar.reset_button.click(reset_event, inputs=[state], outputs=[state, *fresh_chat], **HIDE)
            sidebar.history.click(history_event, inputs=[state], outputs=[area.chatbot, area.examples], **HIDE)
            for button, template in zip(area.example_buttons, EXAMPLE_QUESTIONS):
                button.click(lambda t=template: self.show_examples(t)[1], inputs=None, outputs=[area.message], **HIDE)

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
        message = (message or "").strip()
        history = list(history or [])
        state = state if state is not None else {}
        if not message or state.get("user") is None:   # 빈 질문·로그인 전에는 처리하지 않음
            return history, state
        answer = self._answer(message, state)
        history += [
            {"role": "user", "content": render_user_message(message)},
            {"role": "assistant", "content": self.render_answer(answer)},
        ]
        # 질문·답변을 MySQL chat_history 에 저장 (사이드바 대화 기록). 오류 안내도 사진 답변(on_image)과 같이 남긴다
        state = self._conversation.save_history(state, {"question": message, "answer": answer.model_dump(mode="json")})
        return history, state

    def _answer(self, question: str, state: dict, input_type: str = "text") -> AnswerResponse:
        """RagPipeline.answer 를 불러 AnswerResponse 로 만든다. 파이프라인이 없거나 예외·형식 오류여도 안내 응답을 돌려줘서 화면이 멈추지 않게 한다.

        RagPipeline.answer 는 chat_logs(요청 기록)를 스스로 저장하므로 여기서는 저장하지 않는다.
        """
        print(f"[gradio_app] 질문 받음 ({input_type}): {question!r}")
        if self._pipeline is None:
            print("[gradio_app] 파이프라인이 없습니다 (app.py 시작 로그의 '[app] 질문 처리 파이프라인을 만들지 못했습니다' 확인)")
            return AnswerResponse(status="error", input_type=input_type, message=MSG_NO_PIPELINE)
        try:
            raw = self._pipeline.answer(question, state, input_type)
            self._log_answer(raw)
            return AnswerResponse.model_validate(raw)
        except ValidationError as e:   # answer 는 스스로 형식을 검사하므로 거의 없음. 있으면 화면 전체가 깨지지 않게 안내만 보여 줌
            print(f"[gradio_app] 응답 형식 오류: {e.error_count()}건 {e}")
            return AnswerResponse(status="error", input_type=input_type, message=MSG_BAD_ANSWER)
        except Exception as e:  # noqa: BLE001 — answer 가 예외를 밖으로 내지 않는 게 원칙이지만, 어떤 경우에도 화면은 계속 동작
            print(f"[gradio_app] 답변 생성 실패: {type(e).__name__}: {e}")
            traceback.print_exc()
            return AnswerResponse(status="error", input_type=input_type, message=MSG_ANSWER_ERROR)

    @staticmethod
    def _log_answer(raw: dict) -> None:
        """파이프라인이 돌려준 응답을 한 줄로 찍는다 (답을 못 만든 원인 찾기용). status 가 answered 가 아니면 trace 도 함께."""
        raw = raw or {}
        trace = raw.get("trace") or {}
        print(f"[gradio_app] 응답 status={raw.get('status')} type={raw.get('question_type')} "
              f"top_score={trace.get('top_score')} threshold={trace.get('threshold')} latency_ms={trace.get('latency_ms')}")
        if raw.get("status") != "answered":
            print(f"[gradio_app]   message={raw.get('message')!r} error={trace.get('error')} "
                  f"queries_en={trace.get('queries_en')} hits={[(h.get('id'), h.get('score')) for h in trace.get('hits') or []]}")

    def on_image(self, image: bytes, history: list, state: dict):
        """
        업로드한 사진을 입력 판별 후 명판이면 설비 설정(설비 표시), 손상 사진이면 답변 생성, 그 외이면 재촬영 안내

        받는 것:
          image   : 사진 파일 내용 (bytes)
          history : 채팅창 기록 (Gradio messages). 사진 말풍선은 화면(build_ui)이 붙이고, 여기서는 답변 카드만 붙인다
          state   : 대화 상태. 명판이 models.csv 모델과 정확히 맞을 때만 state["equipment"] 를 바꾼다
        결과는 응답 스키마(AnswerResponse)로 만들어 render_answer 로 그린다 (텍스트 답변과 같은 카드 모양).
        Returns: list, dict
        """
        history = list(history or [])
        state = state if state is not None else {}
        if not image or state.get("user") is None:   # 빈 사진·로그인 전에는 처리하지 않음 (on_submit 과 같은 규칙)
            return history, state
        try:
            answer, state = self._analyze_photo(image, state)
        except Exception as e:  # 비전 API 오류 등: 화면이 멈추지 않고 안내 카드만 보여 줌
            print(f"[gradio_app] 사진 분석 실패: {type(e).__name__}: {e}")
            answer = AnswerResponse(status="error", input_type="other_photo", message=MSG_PHOTO_ERROR)
        history.append({"role": "assistant", "content": self.render_answer(answer)})
        turn = {"question": PHOTO_QUESTIONS[answer.input_type], "answer": answer.model_dump(mode="json")}
        state = self._conversation.save_history(state, turn)
        return history, state

    def _vision(self):
        """사진 처리에 쓰는 객체를 처음 사진이 올 때 한 번 만든다 (on_image 전용).
        ChatApp.__init__ 을 바꾸지 않으려고 여기서 만든다. 테스트는 self._vision_parts 에 가짜 객체를 넣어 둔다."""
        if getattr(self, "_vision_parts", None) is None:
            # 무거운 모듈(LangChain 등)은 사진을 처음 받을 때만 불러온다 (로그인 화면만 쓸 때는 필요 없음)
            from chatbot.core.config import Settings
            from chatbot.pipeline import RagPipeline
            from chatbot.rag.glossary import Glossary
            from chatbot.rag.llm import LLMClient
            from chatbot.vision.vision import DamageAnalyzer, ImageRouter, NameplateReader

            llm = LLMClient(self._settings or Settings.load())
            self._vision_parts = {
                "router": ImageRouter(llm), "reader": NameplateReader(llm), "analyzer": DamageAnalyzer(llm),
                "models": Glossary().load_models("data/models.csv"),
                "pipeline": self._pipeline or RagPipeline(llm),   # 손상 사진 답변도 텍스트 질문과 같은 파이프라인을 씀
            }
        return self._vision_parts

    def _analyze_photo(self, image, state):
        """사진 종류 판별 → 명판 / 손상 / 그 외로 나눔. Returns: (AnswerResponse, state)"""
        kind, confidence = self._vision()["router"].classify_image(image)
        if kind == "other":
            return AnswerResponse(status="retake", input_type="other_photo", message=MSG_PHOTO_OTHER,
                                  image=ImageResult(kind=kind, confidence=confidence)), state
        if kind == "nameplate":
            return self._nameplate_answer(image, confidence, state)
        return self._damage_answer(image, confidence, state)

    def _nameplate_answer(self, image, confidence, state):
        """명판 판독 → models.csv 매칭. 정확히 맞을 때만 설비를 설정한다. Returns: (AnswerResponse, state)"""
        reader, models = self._vision()["reader"], self._vision()["models"]
        read = reader.read_nameplate(image)
        model, match = (None, "none") if read["retake"] else reader.match_model(read["raw_text"], models)
        result = NameplateResult(raw_text=read["raw_text"], serial=read["serial"], maker=read["maker"],
                                 matched_model=model.model_name if model else None, match=match,
                                 confidence=read["confidence"])
        image_result = ImageResult(kind="nameplate", confidence=confidence, nameplate=result)

        def reply(status, message, equipment=None):
            return AnswerResponse(status=status, input_type="nameplate_photo", message=message,
                                  equipment=equipment, image=image_result)

        if read["retake"] or (match == "none" and self._several_near(reader, read["raw_text"], models)):
            # 한 글자 차이 후보가 여럿이면(L800 → L400·L500) 목록에 있는 모델을 잘못 읽었을 가능성이 커서 다시 찍게 한다
            return reply("retake", MSG_NAMEPLATE_RETAKE), state
        if match == "exact":
            state = self._conversation.set_equipment(state, model)
            equipment = Equipment(model=model.model_name, doc_ids=model.doc_ids, source="nameplate")
            return reply("nameplate", f"명판에서 {model.model_name} 설비를 확인했습니다. "
                                      f"이제부터 {model.model_name} 매뉴얼 기준으로 답변합니다.", equipment), state
        if match == "near":
            # 되묻기(다음 입력이 이 질문에 기대는 것)는 하지 않는다. 모델명을 넣어 질문하면 질문 이해 단계가 설비를 잡는다
            return reply("nameplate", f"명판을 {read['raw_text']}(으)로 읽었습니다. {model.model_name} 설비가 맞다면 "
                                      f"질문에 모델명을 넣어 물어봐 주세요 (예: {model.model_name} 벨트가 미끄러져요). "
                                      "아니면 명판을 다시 찍어 주세요."), state
        current = (state.get("equipment") or {}).get("model_name") or "전체 매뉴얼"
        return reply("nameplate", f"명판의 모델 {read['raw_text']}은(는) 등록된 매뉴얼 목록에 없습니다. "
                                  f"설비는 바꾸지 않고 {current} 기준으로 답변합니다."), state

    def _several_near(self, reader, raw_text, models):
        """모델명·별칭 중 한 글자 차이인 모델이 둘 이상인지 (match_model 이 none 을 돌려준 이유가 '후보가 여럿'인지 구분)"""
        target = reader.normalize_model(raw_text)
        names = {m.model_name for key, m in models.items()
                 if reader._one_char_apart(target, reader.normalize_model(key))}
        return len(names) >= 2

    def _damage_answer(self, image, confidence, state):
        """손상 분석 → 증상 문장으로 RagPipeline.answer. Returns: (AnswerResponse, state)"""
        parts = self._vision()
        damage = parts["analyzer"].describe_damage(image)
        image_result = ImageResult(kind="belt_damage", confidence=confidence, damage=DamageResult(
            label=damage["label"], symptom_ko=damage["symptom_ko"], confidence=damage["confidence"]))
        if damage["retake"]:
            return AnswerResponse(status="retake", input_type="damage_photo", message=MSG_DAMAGE_RETAKE,
                                  image=image_result), state
        try:
            # 증상 문장을 질문처럼 검색한다. damage_map 의 KB 후보를 넘길 자리는 answer 에 아직 없음 (파이프라인 담당과 협의)
            answer = parts["pipeline"].answer(damage["symptom_ko"], state, "damage_photo")
            if not isinstance(answer, AnswerResponse):
                answer = AnswerResponse.model_validate(answer)
            answer = answer.model_copy(update={"image": image_result})   # 화면·로그에서 사진 분석 결과도 함께 보이게
        except NotImplementedError:
            # RagPipeline.answer 가 아직 없으면 분석 결과만 알려 준다 (answer 가 완성되면 코드 수정 없이 답변이 나옴)
            answer = AnswerResponse(status="error", input_type="damage_photo", image=image_result,
                                    message=f"사진 분석: {damage['symptom_ko']} (답변 기능 준비 중)")
        return answer, state

    def on_follow_up(self, question: str, history: list, state: dict):
        """
        답변 아래 후속 질문 버튼 클릭을 받아 그 문장을 새 질문으로 처리. 후속 질문은 설비·부품이 들어간 완전한 문장이라 이전 대화 맥락이 필요 없음

        Returns: list, dict
        """
        raise NotImplementedError("ChatApp.on_follow_up")

    def show_examples(self, template: str | None):
        """
        예시 질문 템플릿 목록을 출력하고, 클릭한 템플릿을 입력창에 추가

        Returns: list[str] (예시 질문 목록), str (입력창에 넣을 문장. 누른 칩이 없으면 "")
        """
        # 목록은 ui/chat/chat_area.py 의 EXAMPLE_QUESTIONS (KB 로 답할 수 있는 질문 5개). 칩도 같은 목록으로 만든다.
        return list(EXAMPLE_QUESTIONS), (template or "").strip()

    def show_history(self, state: dict):
        """
        로그인한 사원의 과거 대화 기록을 ConversationState.load_history로 MySQL에서 불러와 목록(로그창)으로 출력

        Returns: list
        """
        user = state.get("user")
        return self._conversation.load_history(user.employee_id) if user is not None else []

    def open_history(self, state: dict, index, key: str | None = None):
        """
        대화 기록 목록의 항목을 누르면 그 대화의 질문·답변을 채팅창에 다시 보여 줌 (C-03). index 는 목록 순서(최신순),
        key 는 항목에 달아 둔 history_key(저장 시각 + 질문). 목록을 그린 뒤 새 대화가 저장돼 순서가 밀렸으면 key 로 다시 찾음

        Returns: list | None (채팅창 기록: 질문 말풍선 + 답변 카드. 찾지 못하면 None)
        """
        rows = self.show_history(state or {})
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = -1
        row = rows[index] if 0 <= index < len(rows) else None
        if key is not None and (row is None or history_key(row) != key):
            row = next((r for r in rows if history_key(r) == key), None)
        if row is None:
            return None
        answer = row.get("answer")
        if isinstance(answer, (str, bytes)):   # DB 드라이버에 따라 JSON 칸이 글자로 올 수 있음
            try:
                answer = json.loads(answer)
            except ValueError:
                answer = None
        return [
            {"role": "user", "content": render_user_message(str(row.get("question") or ""))},
            {"role": "assistant", "content": self.render_answer(answer or {})},   # 깨진 답변은 안내 카드로 보임
        ]

    def reset_conversation(self, state: dict):
        """
        대화 초기화: 설비 필터를 비우고 화면의 대화 내용을 지워 전체 매뉴얼 검색으로 되돌림

        Returns: dict
        """
        # 설비 필터는 ConversationState.clear 가 비우고, 화면의 채팅창은 build_ui 의 reset_event 가 [] 로 지움
        return self._conversation.clear(state)

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

    def render_answer(self, answer: dict | AnswerResponse):
        """
        응답 JSON을 화면용 텍스트로 변환 (요약 → 안전 문구 → 원인·조치 → 출처·원문 → 그림 순서, 후속 질문 버튼 포함), 원문 보기에서
        근거가 표·그림이면 잘라 둔 이미지를 같이 표시

        Returns: str
        """
        # 뼈대: 카드 모양은 ui/chat/messages.py. 원문 보기의 표·그림 이미지와 후속 질문 버튼은 3.7.4 에서 붙인다.
        # 응답 dict 는 여기서 AnswerResponse 로 한 번만 검사한다. 형식이 틀려도 화면 전체가 깨지지 않고 안내 카드만 보인다.
        if not isinstance(answer, AnswerResponse):
            try:
                answer = AnswerResponse.model_validate(answer)
            except ValidationError as e:
                print(f"[gradio_app] 응답 형식 오류: {e.error_count()}건")
                answer = AnswerResponse(status="error", input_type="text", message=MSG_BAD_ANSWER)
        return render_answer_card(answer)
