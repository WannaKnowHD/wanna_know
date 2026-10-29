"""관리자 대시보드 (운영 현황·답변 품질·시스템·KB 현황)"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr
import pandas as pd

from chatbot.ui.admin.usage_tab import build_usage_tab
from chatbot.ui.admin.quality_tab import build_quality_tab
from chatbot.ui.admin.system_tab import build_system_tab
from chatbot.ui.admin.kb_tab import build_kb_tab

if TYPE_CHECKING:
    from chatbot.core.schemas import User
    from chatbot.db.db import Database
    from datetime import date
    from pandas import DataFrame

# 관리자 화면 스타일. Gradio 6은 css를 launch(css_paths=[ADMIN_CSS_PATH])로 넘김
ADMIN_CSS_PATH = Path(__file__).parent / "admin" / "admin.css"

# load_logs 가 돌려주는 열 (chat_logs 열 + kb_gaps 검토 상태). 통계 함수(usage_stats · quality_stats · not_found_list)가 이 이름을 씀
LOG_TABLE_COLUMNS = [
    "log_id", "created_at", "employee_id", "question", "input_type", "question_type", "equipment_model",
    "kb_ids", "top_score", "not_found", "safety_included", "confidence", "latency_ms", "tokens_in",
    "tokens_out", "error", "reviewed", "kb_update_needed",
]
LOG_JSON_COLUMNS = ("kb_ids", "latency_ms")
LOG_BOOL_COLUMNS = ("not_found", "safety_included", "reviewed", "kb_update_needed")
LOAD_LOGS_SQL = (
    "SELECT l.log_id, l.created_at, l.employee_id, l.question, l.input_type, l.question_type, "
    "l.equipment_model, l.kb_ids, l.top_score, l.not_found, l.safety_included, l.confidence, "
    "l.latency_ms, l.tokens_in, l.tokens_out, l.error, "
    "COALESCE(g.reviewed, 0) AS reviewed, COALESCE(g.kb_update_needed, 0) AS kb_update_needed "
    "FROM chat_logs l LEFT JOIN kb_gaps g ON g.log_id = l.log_id "
    "WHERE l.source = 'user' AND l.created_at >= %s AND l.created_at < %s "
    "ORDER BY l.created_at DESC, l.log_id DESC"
)

class AdminDashboard:
    """AdminDashboard (기능명세서 기준 뼈대)."""

    @staticmethod
    def render_user_info(user: User | None) -> str:
        """
        관리자 헤더 오른쪽의 사원 정보(이름·사원번호) HTML

        Returns: str
        """
        name = escape(user.name) if user else "-"
        employee_id = escape(user.employee_id) if user else ""
        return (
            "<div class='admin-user'>"
            f"<span class='admin-user-name'>관리자 · {name}</span>"
            f"<span class='admin-user-id'>{employee_id}</span>"
            "</div>"
        )

    def load_logs(self, start: date, end: date, db: Database):
        """
        선택한 기간의 질의 로그를 MySQL chat_logs 테이블에서 읽어 표로 변환 (운영 현황 탭의 기간 선택과 연결).
        평가 로그(source='eval')는 제외. kb_gaps를 log_id로 LEFT JOIN해 reviewed·kb_update_needed 열을 함께 담음
        (kb_gaps에 행이 없으면 false)

        Returns: DataFrame
        """
        start, end = _as_date(start), _as_date(end)
        if start > end:
            start, end = end, start
        # end 당일 기록까지 포함: end 다음 날 00:00 미만
        begin = datetime.combine(start, time.min).strftime("%Y-%m-%d %H:%M:%S")
        until = datetime.combine(end + timedelta(days=1), time.min).strftime("%Y-%m-%d %H:%M:%S")
        rows = db.fetch(LOAD_LOGS_SQL, (begin, until))

        logs = pd.DataFrame(rows, columns=LOG_TABLE_COLUMNS)
        if logs.empty:
            return logs
        logs["created_at"] = pd.to_datetime(logs["created_at"])
        for column in LOG_JSON_COLUMNS:   # MySQL(db.fetch)는 이미 풀어 주고, sqlite 는 글자로 옴
            empty = [] if column == "kb_ids" else {}
            logs[column] = logs[column].map(lambda v, empty=empty: _from_json(v, empty))
        for column in LOG_BOOL_COLUMNS:   # MySQL BOOLEAN 은 0 · 1 로 옴
            logs[column] = logs[column].fillna(0).astype(bool)
        return logs

    def usage_stats(self, logs: DataFrame):
        """
        운영 현황 탭: 일별 질문 수, 입력 유형 비율(텍스트·명판 사진·손상 사진·그 외 사진), 설비 모델별 질문 수, 자주 묻는 증상 Top 10

        Returns: dict
        """
        raise NotImplementedError("AdminDashboard.usage_stats")

    def quality_stats(self, logs: DataFrame):
        """
        답변 품질 탭: 답을 못 찾은 비율, 안전 문구 포함률

        Returns: dict
        """
        raise NotImplementedError("AdminDashboard.quality_stats")

    def system_stats(self, logs: DataFrame, results_dir: str):
        """
        시스템 탭: 단계별 응답 시간(p50·p95), 오류 비율, 평가 버전별 점수(v0~v3)

        Returns: dict
        """
        raise NotImplementedError("AdminDashboard.system_stats")

    def not_found_list(self, logs: DataFrame):
        """
        답변 품질 탭 하위: chat_logs에서 답을 못 찾은 질문(not_found=true, source='user')과 kb_gaps의 검토 상태를 합쳐
        목록(시각·질문·설비·검색 점수·담당자 확인·KB 업데이트 필요) 출력. 검토 상태는 load_logs가 kb_gaps를 LEFT JOIN해
        담아 둔 reviewed·kb_update_needed 열을 사용. 담당자 확인·KB 업데이트 필요는 체크박스 열로 표시

        Returns: DataFrame
        """
        raise NotImplementedError("AdminDashboard.not_found_list")

    def save_review(
        self, log_id: int, reviewed: bool, kb_update_needed: bool, user: User
    ):
        """
        체크박스를 바꾸면 해당 로그의 담당자 확인·KB 업데이트 필요·처리자·처리 시각을 MySQL kb_gaps 테이블에 저장
        (log_id 기준으로 있으면 갱신, 없으면 추가). 실행 전 관리자 권한 확인

        Returns: bool
        """
        raise NotImplementedError("AdminDashboard.save_review")

    def kb_status(self, db: Database):
        """
        KB 현황 탭: MySQL 문서 테이블에서 현재 청킹된 PDF 목록(문서명, 페이지 수, 청크 수, 색인 시각, 상태) 출력

        Returns: DataFrame
        """
        raise NotImplementedError("AdminDashboard.kb_status")

    def add_pdf(self, pdf: bytes, filename: str):
        """
        업로드한 PDF를 data/raw/admin_add/에 저장 → documents 상태를 '색인 중'으로 기록 → IndexBuilder.main 호출
        (전체 재생성, 캐시 재사용) → 완료/실패와 사유 기록 → 목록 상태 표시. 재생성 중에는 기존 색인으로 답변

        Returns: bool, str
        """
        raise NotImplementedError("AdminDashboard.add_pdf")

    def build_admin_tab(self):
        """
        관리자 대시보드 화면 구성 (운영 현황·답변 품질·시스템·KB 현황 4개 탭, gr.Dataframe·gr.BarPlot·gr.LinePlot).
        관리자 모드일 때만 표시

        Returns: gr.Column
        """

        # 관리자 화면 공통 레이아웃 (스타일: ADMIN_CSS_PATH)
        # 화면 전환은 ChatApp에서 visible로 처리하므로 상위 탭 없이 Column으로 구성
        with gr.Column(elem_id="admin-root") as admin_view:
            # 관리자 공통 헤더: 로고·관리자 모드 / 사원 정보·채팅 복귀·로그아웃
            with gr.Row(elem_id="admin-header", equal_height=True):
                gr.HTML(
                    "<div class='admin-brand-inner'>"
                    "<span class='admin-brand-logo'>WANNA KNOW</span>"
                    "<span class='admin-mode-chip'>관리자 모드</span>"
                    "</div>",
                    elem_classes=["admin-brand"],
                )
                # 로그인한 사원 정보는 ChatApp에서 render_user_info로 갱신
                self.user_info_display = gr.HTML(self.render_user_info(None))
                self.back_button = gr.Button(
                    "채팅 화면으로",
                    elem_classes=["admin-header-button"],
                )
                self.logout_button = gr.Button(
                    "로그아웃",
                    elem_classes=["admin-header-button"],
                )

            # 관리자 내부 탭 구성
            with gr.Tabs(elem_id="admin-tabs"):
                # 운영 현황 탭
                build_usage_tab()

                # 답변 품질 탭
                build_quality_tab()

                # 시스템 현황 탭
                build_system_tab()

                # KB 현황 탭
                build_kb_tab()

        return admin_view

def _as_date(value) -> date:
    """date · datetime · "YYYY-MM-DD" 를 date 로"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _from_json(value, empty):
    """JSON 칸 값: 글자면 풀고, 비어 있으면 empty"""
    if value is None or value == "":
        return empty
    if isinstance(value, (bytes, str)):
        try:
            return json.loads(value)
        except ValueError:
            return empty
    return value