"""관리자 대시보드 (운영 현황·답변 품질·시스템·KB 현황)"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr
import pandas as pd

from chatbot.ui.admin.kb_files import KB_STATUS_COLUMNS, KbIndexer, load_documents
from chatbot.ui.admin.kb_tab import build_kb_tab, render_kb_header, render_kb_table
from chatbot.ui.admin.quality_tab import (
    KB_UPDATE_NEEDED,
    REVIEWED,
    build_quality_tab,
    render_quality_tab,
)
from chatbot.ui.admin.system_tab import (
    aggregate_system_stats,
    build_system_tab,
    render_system_tab,
)
from chatbot.ui.admin.usage_tab import build_usage_tab, render_usage_tab

if TYPE_CHECKING:
    from datetime import date

    from pandas import DataFrame

    from chatbot.core.schemas import User
    from chatbot.db.db import Database

logger = logging.getLogger(__name__)

# 관리자 화면 스타일. Gradio 6은 css를 launch(css_paths=[ADMIN_CSS_PATH])로 넘김
ADMIN_CSS_PATH = Path(__file__).parent / "admin" / "admin.css"

# load_logs 가 돌려주는 열 (chat_logs 열 + kb_gaps 검토 상태). 통계 함수(usage_stats · quality_stats · not_found_list)가 이 이름을 씀
LOG_TABLE_COLUMNS = [
    "log_id",
    "created_at",
    "employee_id",
    "question",
    "input_type",
    "question_type",
    "equipment_model",
    "kb_ids",
    "top_score",
    "not_found",
    "safety_included",
    "confidence",
    "latency_ms",
    "error",
    "reviewed",
    "kb_update_needed",
]
LOG_JSON_COLUMNS = ("kb_ids", "latency_ms")
LOG_BOOL_COLUMNS = ("not_found", "safety_included", "reviewed", "kb_update_needed")
LOAD_LOGS_SQL = (
    "SELECT l.log_id, l.created_at, l.employee_id, l.question, l.input_type, l.question_type, "
    "l.equipment_model, l.kb_ids, l.top_score, l.not_found, l.safety_included, l.confidence, "
    "l.latency_ms, l.error, "
    "COALESCE(g.reviewed, 0) AS reviewed, COALESCE(g.kb_update_needed, 0) AS kb_update_needed "
    "FROM chat_logs l LEFT JOIN kb_gaps g ON g.log_id = l.log_id "
    "WHERE l.source = 'user' AND l.created_at >= %s AND l.created_at < %s "
    "ORDER BY l.created_at DESC, l.log_id DESC"
)
# save_review: kb_gaps.log_id 가 UNIQUE 라서 INSERT ... ON DUPLICATE KEY UPDATE 한 번으로 "있으면 갱신, 없으면 추가" (schema.sql 주석)
SAVE_REVIEW_SQL = (
    "INSERT INTO kb_gaps (log_id, reviewed, kb_update_needed, reviewed_by, reviewed_at) "
    "VALUES (%s, %s, %s, %s, NOW()) "
    "ON DUPLICATE KEY UPDATE reviewed = %s, kb_update_needed = %s, reviewed_by = %s, reviewed_at = NOW()"
)

# 기간 선택: 이름 → 오늘을 포함한 일수. 운영 현황·답변 품질 탭이 같은 기간의 로그를 씀 (Figma 기본: 최근 7일)
PERIODS = {"오늘": 1, "최근 7일": 7, "최근 30일": 30}
DEFAULT_PERIOD = "최근 7일"
ADMIN_TITLE = "관리자 대시보드"  # Figma 06~09 상단 바 왼쪽 제목
BACK_LABEL = "뒤로가기 (채팅 화면)"  # Figma 상단 바 버튼 · 화면설계서 SCR-05 3번

# chat_logs.input_type → 화면 이름 (usage_tab.INPUT_TYPE_ORDER 와 같은 순서)
INPUT_TYPE_LABELS = {
    "text": "텍스트 질문",
    "nameplate": "명판 사진",
    "damage": "손상 사진",
    "other": "그 외 사진",
}
NO_MODEL_LABEL = "미지정(전체)"  # 설비 모델 없이 전체 매뉴얼에서 찾은 질문
TOP_SYMPTOMS = 10


class AdminDashboard:
    """AdminDashboard (기능명세서 기준 뼈대)."""

    def __init__(
        self,
        db: Database | None = None,
        kb_symptoms: dict[str, str] | None = None,
        kb_symptoms_ko: dict[str, str] | None = None,
    ):
        """
        받는 것:
          db          : 로그를 읽고(load_logs) 검토 상태를 저장(save_review)할 Database. 없으면 화면 틀만 만듦
          kb_symptoms    : kb_id → 증상 문장 (load_kb_symptoms, KB 원문 영어). 자주 묻는 증상 Top 10 에 씀
          kb_symptoms_ko : kb_id → 한국어 증상 (load_kb_symptoms_ko). 있으면 Top 10 에 한국어를 크게, 영어 원문을 아래 줄에 작게 보임
        """
        self.db = db
        self.kb_symptoms = kb_symptoms or {}
        self.kb_symptoms_ko = kb_symptoms_ko or {}

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
        until = datetime.combine(end + timedelta(days=1), time.min).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = db.fetch(LOAD_LOGS_SQL, (begin, until))

        logs = pd.DataFrame(rows, columns=LOG_TABLE_COLUMNS)
        if logs.empty:
            return logs
        logs["created_at"] = pd.to_datetime(logs["created_at"])
        for (
            column
        ) in LOG_JSON_COLUMNS:  # MySQL(db.fetch)는 이미 풀어 주고, sqlite 는 글자로 옴
            empty = [] if column == "kb_ids" else {}
            logs[column] = logs[column].map(lambda v, empty=empty: _from_json(v, empty))
        for column in LOG_BOOL_COLUMNS:  # MySQL BOOLEAN 은 0 · 1 로 옴
            logs[column] = logs[column].fillna(0).astype(bool)
        return logs

    def usage_stats(
        self, logs: DataFrame, start: date | None = None, end: date | None = None
    ):
        """
        운영 현황 탭: 일별 질문 수, 입력 유형 비율(텍스트·명판 사진·손상 사진·그 외 사진), 설비 모델별 질문 수, 자주 묻는 증상 Top 10.
        logs 는 load_logs 결과. start·end(load_logs 와 같은 기간)를 주면 질문이 없는 날도 0 으로 채움

        Returns: dict (total, daily, input_types, models, top_symptoms. 표 값은 화면에 바로 넣는 DataFrame)
        """
        total = len(logs)
        days = pd.to_datetime(
            logs["created_at"]
        ).dt.date  # 빈 기간이면 load_logs 가 형 변환 없이 돌려줌
        first = (
            _as_date(start)
            if start is not None
            else (days.min() if total else date.today())
        )
        last = (
            _as_date(end)
            if end is not None
            else (days.max() if total else date.today())
        )
        if first > last:  # load_logs 처럼 거꾸로 넣어도 같은 기간
            first, last = last, first
        all_days = pd.date_range(first, last, freq="D").date
        day_counts = days.value_counts()
        daily = pd.DataFrame(
            {
                "날짜": [f"{day:%m/%d}" for day in all_days],
                "질문 수": [int(day_counts.get(day, 0)) for day in all_days],
            }
        )

        type_counts = logs["input_type"].value_counts()
        input_types = pd.DataFrame(
            {
                "입력 유형": list(INPUT_TYPE_LABELS.values()),
                "건수": [int(type_counts.get(key, 0)) for key in INPUT_TYPE_LABELS],
            }
        )
        input_types["비율(%)"] = (
            (input_types["건수"] / total * 100).round(1) if total else 0.0
        )

        models = (
            logs["equipment_model"]
            .fillna(NO_MODEL_LABEL)
            .replace("", NO_MODEL_LABEL)
            .value_counts()
            .rename_axis("설비 모델")
            .reset_index(name="질문 수")
        )
        return {
            "total": total,
            "daily": daily,
            "input_types": input_types,
            "models": models,
            "top_symptoms": self._top_symptoms(logs),
        }

    def quality_stats(self, logs: DataFrame):
        """
        답변 품질 탭: 답을 못 찾은 비율, 안전 문구 포함률.
        안전 문구 포함률은 실제로 답변한 질문(검색을 거쳤고 못 찾음·오류가 아닌 질문) 기준

        Returns: dict (total, not_found, not_found_rate, answered, safety_included, safety_rate)
        """
        total = len(logs)
        not_found_rows = logs["not_found"].astype(
            bool
        )  # 빈 기간이면 load_logs 가 bool 로 바꾸지 않은 채 돌려줌
        not_found = int(not_found_rows.sum())
        # 명판 인식·재촬영 안내는 검색을 하지 않아 top_score 가 비어 있으므로 빠짐
        answered_rows = (
            logs["top_score"].notna() & ~not_found_rows & logs["error"].isna()
        )
        answered = int(answered_rows.sum())
        safety_included = int(logs.loc[answered_rows, "safety_included"].sum())
        return {
            "total": total,
            "not_found": not_found,
            "not_found_rate": not_found / total if total else 0.0,
            "answered": answered,
            "safety_included": safety_included,
            "safety_rate": safety_included / answered if answered else 0.0,
        }

    def system_stats(self, logs: DataFrame, results_dir: str = ""):
        """선택 기간의 운영 로그로 시스템 지표를 집계한다."""
        return aggregate_system_stats(logs)

    def not_found_list(self, logs: DataFrame):
        """
        답변 품질 탭 하위: chat_logs에서 답을 못 찾은 질문(not_found=true, source='user')과 kb_gaps의 검토 상태를 합쳐
        목록(Figma 07 라이트: 날짜·질문·담당자 확인·KB 업데이트 필요) 출력. 검토 상태는 load_logs가 kb_gaps를 LEFT JOIN해
        담아 둔 reviewed·kb_update_needed 열을 사용. 담당자 확인·KB 업데이트 필요는 체크박스 열로 표시.
        맨 뒤 No(log_id)는 저장할 때 행을 찾는 열이라 화면에서는 숨김

        Returns: DataFrame (열은 quality_tab.NOT_FOUND_HEADERS, load_logs 와 같은 최신순)
        """
        rows = logs[logs["not_found"].astype(bool)].sort_values(
            ["created_at", "log_id"], ascending=False
        )
        return pd.DataFrame(
            {
                "날짜": pd.to_datetime(rows["created_at"]).dt.strftime("%m/%d %H:%M"),
                "질문": rows["question"].fillna(""),
                REVIEWED: rows["reviewed"].astype(bool),
                KB_UPDATE_NEEDED: rows["kb_update_needed"].astype(bool),
                "No": rows["log_id"].astype(int),
            }
        ).reset_index(drop=True)

    def save_review(
        self, log_id: int, reviewed: bool, kb_update_needed: bool, user: User
    ):
        """
        체크박스를 바꾸면 해당 로그의 담당자 확인·KB 업데이트 필요·처리자·처리 시각을 MySQL kb_gaps 테이블에 저장
        (log_id 기준으로 있으면 갱신, 없으면 추가). 실행 전 관리자 권한 확인

        Returns: bool (관리자가 아니거나 DB 오류면 False)
        """
        if user is None or not user.is_admin:
            logger.warning(
                "관리자가 아닌 사용자의 검토 상태 저장을 막음 (log_id=%s)", log_id
            )
            return False
        params = (
            log_id,
            reviewed,
            kb_update_needed,
            user.employee_id,
            reviewed,
            kb_update_needed,
            user.employee_id,
        )
        try:
            self.db.execute(SAVE_REVIEW_SQL, params)
        except Exception:
            # 저장에 실패하면 화면의 체크를 되돌리고 안내 (on_review_input).
            # db.py 의 오류 종류가 정해지면(4.2.6 예외 점검) 그 오류로 좁힘
            logger.exception("검토 상태 저장 실패 (log_id=%s)", log_id)
            return False
        return True

    def kb_status(self, db: Database):
        """
        KB 현황 탭: MySQL 문서 테이블에서 현재 청킹된 PDF 목록(문서명, 페이지 수, 청크 수, 색인 시각, 상태) 출력

        받는 것: Database
        돌려주는 것: DataFrame (문서명 · 파일명 · 구분 · 쪽 수 · 청크 수 · 색인 시각 · 상태 · 실패 사유)
        Returns: DataFrame
        """
        return load_documents(db)

    def add_pdf(self, pdf: bytes, filename: str, user: User | None = None):
        """
        업로드한 PDF를 data/raw/admin_add/에 저장 → documents 상태를 '색인 중'으로 기록 → IndexBuilder.main 호출
        (전체 재생성, 캐시 재사용) → 완료/실패와 사유 기록 → 목록 상태 표시. 재생성 중에는 기존 색인으로 답변

        받는 것: PDF 내용(bytes), 파일 이름, 로그인 사원
        돌려주는 것: (색인을 시작했는지, 화면 안내 문구)
        관리자 함수는 화면에서 숨겨도 API 로 불릴 수 있어 함수 안에서 관리자 권한 확인.
        검사(PDF 아님 · 깨짐 · 암호 · 스캔본 · 이름 겹침)에서 걸리면 저장도 색인도 하지 않음 (kb_files.check_pdf)
        Returns: bool, str
        """
        if user is None or not user.is_admin:
            return False, "관리자만 PDF 를 추가할 수 있습니다."
        if self.db is None:
            return False, "DB 에 연결되지 않아 PDF 를 추가할 수 없습니다."
        return self._kb_indexer().add_pdf(self.db, pdf, filename)

    def _kb_indexer(self) -> KbIndexer:
        """PDF 추가 · 색인을 맡는 KbIndexer. 앱에 하나만 두고 처음 쓸 때 만든다 (색인은 한 번에 하나)"""
        if getattr(self, "_kb", None) is None:
            self._kb = KbIndexer()
        return self._kb

    def _kb_table(self) -> DataFrame:
        """KB 현황 목록. DB 가 없으면 빈 표, 읽지 못하면 화면에 안내 (refresh 와 같은 방식)"""
        if self.db is None:
            return pd.DataFrame(columns=KB_STATUS_COLUMNS)
        try:
            return self.kb_status(self.db)
        except Exception as error:
            logger.exception("KB 현황 조회 실패")
            raise gr.Error(
                "문서 목록을 불러오지 못했습니다. 잠시 후 새로고침해 주세요."
            ) from error

    def _wire_kb_tab(self, kb_tab, state: gr.State):
        """
        KB 현황 탭(kb_tab.KbTab)의 탭 열기 · PDF 추가하기 · 자동 갱신을 kb_status · add_pdf 에 연결.
        build_admin_tab 에서 1번 부른다. 모든 동작은 state["user"] 로 관리자 권한을 다시 확인

        Returns: None
        """
        indexer = self._kb_indexer()

        def status_values() -> tuple:
            # 제목줄(건수 · 진행 안내) · 표 · 자동 갱신(색인 중일 때만 켜짐) · 추가 버튼(색인 중이면 잠금)
            running = indexer.is_running()
            table = self._kb_table()
            return (
                render_kb_header(len(table), indexer.progress_text()),
                render_kb_table(table),
                gr.Timer(active=running),
                gr.UploadButton(interactive=not running),
            )

        def show(current: dict | None):
            _require_admin(current)
            if self.db is not None:
                try:  # 목록에 없는 PDF(초기 문서 등)를 지금 색인 기준으로 채움. 색인 중이면 건너뜀
                    indexer.fill_missing_documents(self.db)
                except Exception:
                    logger.exception("KB 현황 목록 채우기 실패")
            return status_values()

        def add(path: str | None, current: dict | None):
            # "PDF 추가하기"로 파일을 고르면 바로 실행. 결과 안내는 표를 가리지 않게 알림(토스트)으로
            user = _require_admin(current)
            if path:
                file = Path(path)
                started, message = self.add_pdf(file.read_bytes(), file.name, user)
                (gr.Info if started else gr.Warning)(message)
            return status_values()

        outputs = kb_tab.outputs()
        kb_tab.tab.select(fn=show, inputs=[state], outputs=outputs)
        kb_tab.timer.tick(fn=show, inputs=[state], outputs=outputs)
        kb_tab.add_button.upload(fn=add, inputs=[kb_tab.add_button, state], outputs=outputs)

    def build_admin_tab(self, state: gr.State | None = None):
        """
        관리자 대시보드 화면 구성 (Figma 06~09): 상단 바(제목 · 관리자 모드 · 관리자 정보 · 뒤로가기 · 로그아웃) 아래에
        운영 현황 · 답변 품질 · 시스템 · KB 현황 4개 탭. 기간 선택은 운영 현황 탭 맨 위의 기간 표시에 있고, 고르면 운영 현황 ·
        답변 품질 탭을 함께 갱신(refresh). state 는 ChatApp 의 대화 상태(gr.State). 갱신 · 검토 저장 때 state["user"] 로 관리자
        권한을 다시 확인

        Returns: gr.Column
        """
        # 화면 전환은 ChatApp 이 visible 로 처리하므로 상위 탭 없이 Column 으로 구성 (스타일: ADMIN_CSS_PATH)
        with gr.Column(elem_id="admin-root") as admin_view:
            with gr.Row(elem_id="admin-header", equal_height=True):
                gr.HTML(
                    "<div class='admin-brand-inner'>"
                    f"<span class='admin-brand-title'>{ADMIN_TITLE}</span>"
                    "<span class='admin-mode-chip'>관리자 모드</span>"
                    "</div>",
                    elem_classes=["admin-brand"],
                )
                # 로그인한 사원 정보는 ChatApp 이 render_user_info 로 갱신
                self.user_info_display = gr.HTML(self.render_user_info(None))
                self.back_button = gr.Button(
                    BACK_LABEL, elem_classes=["admin-header-button"]
                )
                self.logout_button = gr.Button(
                    "로그아웃", elem_classes=["admin-header-button"]
                )

            with gr.Tabs(elem_id="admin-tabs"):
                usage_tab = build_usage_tab(
                    list(PERIODS), DEFAULT_PERIOD
                )  # 운영 현황 (기간 선택 포함)
                quality_tab = build_quality_tab()  # 답변 품질
                system_outputs = build_system_tab()  # 시스템 (3.8.5)
                kb_tab = build_kb_tab()  # KB 현황 (3.8.6, _wire_kb_tab 이 연결)

        session_state = state if state is not None else gr.State({})
        # 마지막으로 그린 "답 못 찾은 질문 목록". 체크박스가 바뀐 행을 찾을 때 비교 기준
        review_snapshot = gr.State(None)
        # ChatApp 은 관리자 전환 뒤 .then(self.refresh, self.refresh_inputs, self.refresh_outputs) 로 처음 값을 채움
        self.period_input = usage_tab.period
        self.refresh_inputs = [self.period_input, session_state]
        self.refresh_outputs = [
            *usage_tab.outputs(),
            *quality_tab.outputs(),
            review_snapshot,
            *system_outputs,
        ]
        self.period_input.change(
            fn=self.refresh,
            inputs=self.refresh_inputs,
            outputs=self.refresh_outputs,
            show_progress="hidden",  # Gradio 처리 중 표시(주황 로딩) 숨김
        )
        quality_tab.not_found_table.input(
            fn=self.on_review_input,
            inputs=[quality_tab.not_found_table, review_snapshot, session_state],
            outputs=[
                quality_tab.not_found_table,
                review_snapshot,
                quality_tab.review_status,
            ],
            show_progress="hidden",
        )

        self._wire_kb_tab(kb_tab, session_state)  # KB 현황 탭 (WBS 3.8.6)

        return admin_view

    def refresh(self, period: str, state: dict | None):
        """
        선택한 기간의 로그를 load_logs 로 한 번 읽어 운영 현황·답변 품질 탭 값을 함께 만듦 (기간 선택·관리자 전환 직후).
        관리자 함수는 화면에서 숨겨도 API 로 불릴 수 있어 함수 안에서 관리자 권한 확인 (아니면 gr.Error)

        Returns: list (refresh_outputs 순서의 화면 값)
        """
        _require_admin(state)
        period = (
            period if period in PERIODS else DEFAULT_PERIOD
        )  # 화면 밖에서 이상한 값이 와도 기본 기간으로
        start, end = period_range(period, date.today())
        try:
            logs = self.load_logs(start, end, self.db)
        except Exception as error:
            # DB 가 끊겨도 화면이 멈추지 않게 안내만 띄움 (문구는 4.2.6 · 6.1.1 에서 확정)
            logger.exception("관리자 대시보드 로그 조회 실패")
            raise gr.Error(
                "로그를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
            ) from error
        not_found = self.not_found_list(logs)
        return [
            *render_usage_tab(self.usage_stats(logs, start, end), start, end),
            *render_quality_tab(self.quality_stats(logs), not_found, period),
            not_found,
            *render_system_tab(self.system_stats(logs), period),
        ]

    def on_review_input(
        self, table: DataFrame, previous: DataFrame | None, state: dict | None
    ):
        """
        답 못 찾은 질문 목록에서 체크박스가 바뀐 행만 save_review 로 kb_gaps 에 저장.
        KB 업데이트 필요는 담당자 확인을 한 질문에만 체크할 수 있음: 담당자 확인 없이 체크하면 되돌리고, 담당자 확인을 풀면 함께 풂.
        previous 는 마지막으로 그린 목록(비교 기준). 저장하지 못한 행은 체크하기 전 값으로 되돌림

        Returns: DataFrame, DataFrame, str (목록, 새 비교 기준, 안내 문구)
        """
        if previous is None:  # 새로고침으로 목록을 그리기 전
            return table, table, ""
        user = _current_user(state)
        result = previous.copy()
        saved = failed = blocked = 0
        for log_id, reviewed, kb_update_needed in find_review_changes(previous, table):
            row = result["No"] == log_id
            if kb_update_needed and not reviewed:
                if _to_bool(result.loc[row, REVIEWED].iloc[0]):   # 담당자 확인을 푼 경우: KB 업데이트 필요도 함께 풂
                    kb_update_needed = False
                else:                                              # 담당자 확인 없이 KB 업데이트 필요만 체크: 되돌림
                    blocked += 1
                    continue
            if self.save_review(log_id, reviewed, kb_update_needed, user):
                result.loc[row, REVIEWED] = reviewed
                result.loc[row, KB_UPDATE_NEEDED] = kb_update_needed
                saved += 1
            else:
                failed += 1
        if failed:
            message = f"{failed}건을 저장하지 못했습니다. 권한과 DB 연결을 확인해 주세요."
        elif blocked:
            message = "KB 업데이트 필요는 담당자 확인을 먼저 체크해야 선택할 수 있습니다."
        elif saved:
            message = f"{saved}건 저장 ({datetime.now():%H:%M:%S})"
        else:
            message = ""
        return result, result, message

    def _top_symptoms(self, logs: DataFrame) -> DataFrame:
        """
        자주 묻는 증상 Top 10: 근거 KB 가 있는 질문을 첫 번째 KB 의 증상(kb_symptoms)으로 묶어 셈.
        질문 문장은 표현이 제각각이라 그대로 묶으면 같은 증상이 흩어짐 (응답 스키마 9.2절).
        한국어 증상(kb_symptoms_ko)이 있으면 증상 칸에 한국어, 원문 칸에 영어 원문을 둠 (없으면 증상 칸에 원문, 원문 칸은 빈칸)

        Returns: DataFrame (순위, 증상, 원문, 질문 수)
        """
        first_kb = logs["kb_ids"].map(lambda kb_ids: kb_ids[0] if isinstance(kb_ids, list) and kb_ids else None).dropna()
        symptoms = first_kb.map(lambda kb_id: self.kb_symptoms.get(kb_id, kb_id))
        counts = symptoms.value_counts().head(TOP_SYMPTOMS)
        korean: dict[str, str] = {}   # 원문 → 한국어 (같은 원문을 쓰는 KB 가 여럿이면 처음 것)
        for kb_id, text in zip(first_kb, symptoms):
            korean.setdefault(text, self.kb_symptoms_ko.get(kb_id, ""))
        texts = [str(text) for text in counts.index]
        return pd.DataFrame({
            "순위": range(1, len(counts) + 1),
            "증상": [korean.get(text) or text for text in texts],
            "원문": [text if korean.get(text) else "" for text in texts],
            "질문 수": counts.to_numpy(),
        })


def period_range(period: str, today: date) -> tuple[date, date]:
    """
    기간 이름(PERIODS)을 load_logs 에 넘길 시작일·종료일로 변환. 종료일은 오늘

    Returns: date, date
    """
    return today - timedelta(days=PERIODS[period] - 1), today


def find_review_changes(
    previous: DataFrame, current: DataFrame
) -> list[tuple[int, bool, bool]]:
    """
    답 못 찾은 질문 목록에서 체크박스 값이 바뀐 행의 (log_id, 담당자 확인, KB 업데이트 필요) 목록

    Returns: list[tuple[int, bool, bool]]
    """
    before = {
        int(row["No"]): (_to_bool(row[REVIEWED]), _to_bool(row[KB_UPDATE_NEEDED]))
        for _, row in previous.iterrows()
    }
    changes = []
    for _, row in current.iterrows():
        try:
            log_id = int(row["No"])
        except (TypeError, ValueError):  # 화면에서 빈 행을 새로 만든 경우
            continue
        after = (_to_bool(row[REVIEWED]), _to_bool(row[KB_UPDATE_NEEDED]))
        if log_id in before and after != before[log_id]:
            changes.append((log_id, *after))
    return changes


def load_kb_symptoms(kb_dir: str | Path) -> dict[str, str]:
    """
    KB JSON(kb/<종류>/*.json)에서 kb_id → 증상(점검 KB 는 작업) 사전 생성. 자주 묻는 증상 Top 10 에 씀.
    읽지 못한 파일은 건너뜀

    Returns: dict[str, str]
    """
    symptoms: dict[str, str] = {}
    for path in sorted(Path(kb_dir).glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("KB 파일을 읽지 못해 건너뜀: %s", path)
            continue
        for entry in data if isinstance(data, list) else [data]:
            text = entry.get("symptom") or entry.get("task")
            if entry.get("kb_id") and text:
                symptoms[entry["kb_id"]] = text
    return symptoms


def load_kb_symptoms_ko(path: str | Path) -> dict[str, str]:
    """
    한국어 증상 표(data/kb_symptoms_ko.csv: kb_id, symptom_ko)를 kb_id → 한국어 사전으로 읽음. 자주 묻는 증상 Top 10 표시용.
    파일이 없거나 읽지 못하면 빈 사전 (그러면 Top 10 은 영어 원문만 보임)

    Returns: dict[str, str]
    """
    try:
        with open(path, encoding="utf-8-sig", newline="") as file:
            return {row["kb_id"]: row["symptom_ko"].strip() for row in csv.DictReader(file)
                    if row.get("kb_id") and (row.get("symptom_ko") or "").strip()}
    except (OSError, KeyError, csv.Error):
        logger.warning("한국어 증상 표를 읽지 못해 영어 원문만 보임: %s", path)
        return {}


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


def _current_user(state: dict | None) -> User | None:
    """대화 상태(gr.State)에 담긴 로그인 사원"""
    return (state or {}).get("user")


def _require_admin(state: dict | None) -> User:
    """관리자가 아니면 gr.Error 로 막음"""
    user = _current_user(state)
    if user is None or not user.is_admin:
        raise gr.Error("관리자만 볼 수 있습니다.")
    return user


def _to_bool(value) -> bool:
    """체크박스 칸 값(bool · 0/1 · "true"/"false" 글자)을 bool 로"""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return False if pd.isna(value) else bool(value)
