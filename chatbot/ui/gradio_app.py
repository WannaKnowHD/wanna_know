"""로그인·채팅 화면"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import gradio as gr


class ChatApp:
    """ChatApp (기능명세서 기준 뼈대)."""

    def build_ui(self):
        """
        로그인 화면(사원번호·비밀번호 입력창, 로그인 버튼), 채팅 화면(채팅창, 텍스트 입력창, 사진 첨부, 예시 질문 버튼, 대화 기록, 대화 초기화,
        관리자 전환 버튼, 현재 설비 표시, 로그아웃 버튼)과 관리자 대시보드 탭(운영 현황·답변 품질·시스템·KB 현황)을 구성. 로그인 전에는 로그인
        화면만 표시하고, 관리자 전환 버튼은 관리자 계정에게만 표시. 세션 토큰은 gr.BrowserState(고정 저장 키·암호 키)에, 로그인한
        사원(User)은 gr.State에 보관

        Returns: gr.Blocks
        """
        raise NotImplementedError("ChatApp.build_ui")

    def on_load(self, token: str | None):
        """
        페이지를 열거나 새로고침할 때 브라우저에 저장된 토큰으로 AuthManager.restore_session 호출. 유효하면 사원 정보를 대화 상태에
        넣고 채팅 화면 표시(관리자면 관리자 전환 버튼도 표시), 아니면 로그인 화면 표시

        Returns: User | None, dict
        """
        raise NotImplementedError("ChatApp.on_load")

    def on_login(self, employee_id: str, password: str, state: dict):
        """
        로그인 화면에서 입력한 사원번호·비밀번호를 확인. 미입력 → 입력 요청 안내, 불일치 → "사원번호 또는 비밀번호가 올바르지 않습니다" 안내 후
        재입력, 일치 → AuthManager.create_session으로 토큰을 발급해 브라우저(BrowserState)에 저장하고, 사원 정보를 대화
        상태에 저장한 뒤 채팅 화면으로 이동 (관리자 계정이면 관리자 전환 버튼 표시)

        Returns: str (token), dict
        """
        raise NotImplementedError("ChatApp.on_login")

    def on_logout(self, token: str, state: dict):
        """
        로그아웃 버튼 처리: AuthManager.delete_session으로 DB의 세션을 지우고 브라우저의 토큰을 비움.
        ConversationState.clear_user로 사원 정보·관리자 모드·설비 필터와 화면의 대화 내용을 비운 뒤 로그인 화면으로 전환. DB에
        저장된 대화 기록은 지우지 않음

        Returns: bool, dict
        """
        raise NotImplementedError("ChatApp.on_logout")

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
        raise NotImplementedError("ChatApp.show_history")

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
        raise NotImplementedError("ChatApp.change_admin_mode")

    def exit_admin_mode(self, state: dict):
        """
        관리자 모드 해제 후 채팅 화면으로 복귀 (뒤로가기는 모드를 유지한 채 채팅 화면으로 이동)

        Returns: dict
        """
        raise NotImplementedError("ChatApp.exit_admin_mode")

    def render_answer(self, answer: dict):
        """
        응답 JSON을 화면용 텍스트로 변환 (요약 → 안전 문구 → 원인·조치 → 출처·원문 → 그림 순서, 후속 질문 버튼 포함), 원문 보기에서
        근거가 표·그림이면 잘라 둔 이미지를 같이 표시

        Returns: str
        """
        raise NotImplementedError("ChatApp.render_answer")
