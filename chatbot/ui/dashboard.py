"""관리자 대시보드 (운영 현황·답변 품질·시스템·KB 현황)"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.core.schemas import User
    from chatbot.db.db import Database
    from datetime import date
    from pandas import DataFrame
    import gradio as gr


class AdminDashboard:
    """AdminDashboard (기능명세서 기준 뼈대)."""

    def load_logs(self, start: date, end: date, db: Database):
        """
        선택한 기간의 질의 로그를 MySQL query_logs 테이블에서 읽어 표로 변환 (운영 현황 탭의 기간 선택과 연결)

        Returns: DataFrame
        """
        raise NotImplementedError("AdminDashboard.load_logs")

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
        답변 품질 탭 하위: 답을 못 찾은 질문 목록(시각·질문·설비·검색 점수·담당자 확인·KB 업데이트 필요) 출력. 담당자 확인·KB 업데이트 필요는
        체크박스 열로 표시

        Returns: DataFrame
        """
        raise NotImplementedError("AdminDashboard.not_found_list")

    def save_review(self, log_id: int, reviewed: bool, kb_update_needed: bool, user: User):
        """
        체크박스를 바꾸면 해당 로그의 담당자 확인·KB 업데이트 필요·처리자·처리 시각을 MySQL query_logs에 갱신(UPDATE). 실행 전
        관리자 권한 확인

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
        KB 현황 탭의 "PDF 추가하기": 업로드한 PDF를 data/raw/admin_add/에 저장하고
        IndexBuilder.add_document를 호출해 추출 → KB 변환 → 청킹 → 색인. 목록 상태를 "색인 중 → 완료/실패"로 표시

        Returns: bool, str
        """
        raise NotImplementedError("AdminDashboard.add_pdf")

    def build_admin_tab(self):
        """
        관리자 대시보드 화면 구성 (운영 현황·답변 품질·시스템·KB 현황 4개 탭, gr.Dataframe·gr.BarPlot·gr.LinePlot).
        관리자 모드일 때만 표시

        Returns: gr.Tab
        """
        raise NotImplementedError("AdminDashboard.build_admin_tab")
