"""관리자 대시보드 (운영 현황·답변 품질·시스템·KB 현황)"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

import gradio as gr
import pandas as pd

from chatbot.ui.admin.usage_tab import build_usage_tab, render_usage_tab
from chatbot.ui.admin.quality_tab import KB_UPDATE_NEEDED, REVIEWED, build_quality_tab, render_quality_tab
from chatbot.ui.admin.system_tab import build_system_tab
from chatbot.ui.admin.kb_tab import build_kb_tab

if TYPE_CHECKING:
    from chatbot.core.schemas import User
    from chatbot.db.db import Database
    from datetime import date
    from pandas import DataFrame

logger = logging.getLogger(__name__)

# 관리자 화면 스타일. Gradio 6은 css를 launch(css_paths=[ADMIN_CSS_PATH])로 넘김
ADMIN_CSS_PATH = Path(__file__).parent / "admin" / "admin.css"

# load_logs 가 돌려주는 열 (chat_logs 열 + kb_gaps 검토 상태). 통계 함수(usage_stats · quality_stats · not_found_list)가 이 이름을 씀
LOG_TABLE_COLUMNS = [
    "log_id", "created_at", "employee_id", "question", "input_type", "question_type", "equipment_model",
    "kb_ids", "top_score", "not_found", "safety_included", "confidence", "latency_ms",
    "error", "reviewed", "kb_update_needed",
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

# 기간 선택: 이름 → 오늘을 포함한 일수. 운영 현황·답변 품질 탭이 같은 기간의 로그를 씀
PERIODS = {"오늘": 1, "최근 7일": 7, "최근 30일": 30}
DEFAULT_PERIOD = "최근 7일"

# chat_logs.input_type → 화면 이름 (usage_tab.INPUT_TYPE_ORDER 와 같은 순서)
INPUT_TYPE_LABELS = {"text": "텍스트 질문", "nameplate": "명판 사진", "damage": "손상 사진", "other": "그 외 사진"}
NO_MODEL_LABEL = "미지정(전체)"   # 설비 모델 없이 전체 매뉴얼에서 찾은 질문
TOP_SYMPTOMS = 10


class AdminDashboard:
    """AdminDashboard (기능명세서 기준 뼈대)."""

    def __init__(self, db: Database | None = None, kb_symptoms: dict[str, str] | None = None):
        """
        받는 것:
          db          : 로그를 읽고(load_logs) 검토 상태를 저장(save_review)할 Database. 없으면 화면 틀만 만듦
          kb_symptoms : kb_id → 증상 문장 (load_kb_symptoms). 자주 묻는 증상 Top 10 에 씀
        """
        self.db = db
        self.kb_symptoms = kb_symptoms or {}

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

    def usage_stats(self, logs: DataFrame, start: date | None = None, end: date | None = None):
        """
        운영 현황 탭: 일별 질문 수, 입력 유형 비율(텍스트·명판 사진·손상 사진·그 외 사진), 설비 모델별 질문 수, 자주 묻는 증상 Top 10.
        logs 는 load_logs 결과. start·end(load_logs 와 같은 기간)를 주면 질문이 없는 날도 0 으로 채움

        Returns: dict (total, daily, input_types, models, top_symptoms. 표 값은 화면에 바로 넣는 DataFrame)
        """
        total = len(logs)
        days = pd.to_datetime(logs["created_at"]).dt.date   # 빈 기간이면 load_logs 가 형 변환 없이 돌려줌
        first = _as_date(start) if start is not None else (days.min() if total else date.today())
        last = _as_date(end) if end is not None else (days.max() if total else date.today())
        if first > last:   # load_logs 처럼 거꾸로 넣어도 같은 기간
            first, last = last, first
        all_days = pd.date_range(first, last, freq="D").date
        day_counts = days.value_counts()
        daily = pd.DataFrame({
            "날짜": [f"{day:%m/%d}" for day in all_days],
            "질문 수": [int(day_counts.get(day, 0)) for day in all_days],
        })

        type_counts = logs["input_type"].value_counts()
        input_types = pd.DataFrame({
            "입력 유형": list(INPUT_TYPE_LABELS.values()),
            "건수": [int(type_counts.get(key, 0)) for key in INPUT_TYPE_LABELS],
        })
        input_types["비율(%)"] = (input_types["건수"] / total * 100).round(1) if total else 0.0

        models = (
            logs["equipment_model"].fillna(NO_MODEL_LABEL).replace("", NO_MODEL_LABEL)
            .value_counts().rename_axis("설비 모델").reset_index(name="질문 수")
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
        not_found_rows = logs["not_found"].astype(bool)   # 빈 기간이면 load_logs 가 bool 로 바꾸지 않은 채 돌려줌
        not_found = int(not_found_rows.sum())
        # 명판 인식·재촬영 안내는 검색을 하지 않아 top_score 가 비어 있으므로 빠짐
        answered_rows = logs["top_score"].notna() & ~not_found_rows & logs["error"].isna()
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

        Returns: DataFrame (열은 quality_tab.NOT_FOUND_HEADERS, load_logs 와 같은 최신순)
        """
        rows = logs[logs["not_found"].astype(bool)].sort_values(["created_at", "log_id"], ascending=False)
        return pd.DataFrame({
            "No": rows["log_id"].astype(int),
            "시각": pd.to_datetime(rows["created_at"]).dt.strftime("%m/%d %H:%M"),
            "질문": rows["question"].fillna(""),
            "설비": rows["equipment_model"].fillna(NO_MODEL_LABEL),
            "검색 점수": pd.to_numeric(rows["top_score"]).round(2),   # 점수가 모두 비면 숫자 열이 아니라서 바꿔 둠
            REVIEWED: rows["reviewed"].astype(bool),
            KB_UPDATE_NEEDED: rows["kb_update_needed"].astype(bool),
        }).reset_index(drop=True)

    def save_review(
        self, log_id: int, reviewed: bool, kb_update_needed: bool, user: User
    ):
        """
        체크박스를 바꾸면 해당 로그의 담당자 확인·KB 업데이트 필요·처리자·처리 시각을 MySQL kb_gaps 테이블에 저장
        (log_id 기준으로 있으면 갱신, 없으면 추가). 실행 전 관리자 권한 확인

        Returns: bool (관리자가 아니거나 DB 오류면 False)
        """
        if user is None or not user.is_admin:
            logger.warning("관리자가 아닌 사용자의 검토 상태 저장을 막음 (log_id=%s)", log_id)
            return False
        params = (log_id, reviewed, kb_update_needed, user.employee_id, reviewed, kb_update_needed, user.employee_id)
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

    def build_admin_tab(self, state: gr.State | None = None):
        """
        관리자 대시보드 화면 구성 (운영 현황·답변 품질·시스템·KB 현황 4개 탭, gr.Dataframe·gr.BarPlot·gr.LinePlot).
        관리자 모드일 때만 표시. 기간 선택·새로고침은 운영 현황·답변 품질 탭을 함께 갱신(refresh).
        state 는 ChatApp 의 대화 상태(gr.State). 새로고침·검토 저장 때 state["user"] 로 관리자 권한을 다시 확인

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

            # 기간 선택: 운영 현황·답변 품질 탭이 같은 기간의 로그를 씀
            with gr.Row(elem_id="admin-toolbar", equal_height=True):
                self.period_input = gr.Radio(
                    choices=list(PERIODS),
                    value=DEFAULT_PERIOD,
                    show_label=False,
                    container=False,
                    elem_classes=["admin-period"],
                )
                self.refresh_button = gr.Button(
                    "새로고침",
                    size="sm",
                    elem_classes=["admin-toolbar-button"],
                )
                self.updated_note = gr.Markdown(elem_classes=["admin-note"])

            # 관리자 내부 탭 구성
            with gr.Tabs(elem_id="admin-tabs"):
                # 운영 현황 탭
                usage_tab = build_usage_tab()

                # 답변 품질 탭
                quality_tab = build_quality_tab()

                # 시스템 현황 탭
                build_system_tab()

                # KB 현황 탭
                build_kb_tab()

        session_state = state if state is not None else gr.State({})
        # 마지막으로 그린 "답 못 찾은 질문 목록". 체크박스가 바뀐 행을 찾을 때 비교 기준
        review_snapshot = gr.State(None)
        # ChatApp 은 관리자 전환 뒤 .then(self.refresh, self.refresh_inputs, self.refresh_outputs) 로 처음 값을 채움
        self.refresh_inputs = [self.period_input, session_state]
        self.refresh_outputs = [*usage_tab.outputs(), *quality_tab.outputs(), review_snapshot, self.updated_note]
        self.period_input.change(fn=self.refresh, inputs=self.refresh_inputs, outputs=self.refresh_outputs)
        self.refresh_button.click(fn=self.refresh, inputs=self.refresh_inputs, outputs=self.refresh_outputs)
        quality_tab.not_found_table.input(
            fn=self.on_review_input,
            inputs=[quality_tab.not_found_table, review_snapshot, session_state],
            outputs=[quality_tab.not_found_table, review_snapshot, quality_tab.review_status],
        )

        return admin_view

    def refresh(self, period: str, state: dict | None):
        """
        선택한 기간의 로그를 load_logs 로 한 번 읽어 운영 현황·답변 품질 탭 값을 함께 만듦 (기간 선택·새로고침·관리자 전환 직후).
        관리자 함수는 화면에서 숨겨도 API 로 불릴 수 있어 함수 안에서 관리자 권한 확인 (아니면 gr.Error)

        Returns: list (refresh_outputs 순서의 화면 값)
        """
        _require_admin(state)
        start, end = period_range(period, date.today())
        try:
            logs = self.load_logs(start, end, self.db)
        except Exception as error:
            # DB 가 끊겨도 화면이 멈추지 않게 안내만 띄움 (문구는 4.2.6 · 6.1.1 에서 확정)
            logger.exception("관리자 대시보드 로그 조회 실패")
            raise gr.Error("로그를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.") from error
        not_found = self.not_found_list(logs)
        period_label = f"{period} ({start:%m/%d}~{end:%m/%d})"
        return [
            *render_usage_tab(self.usage_stats(logs, start, end), period_label),
            *render_quality_tab(self.quality_stats(logs), not_found),
            not_found,
            f"마지막 갱신 {datetime.now():%H:%M:%S}",
        ]

    def on_review_input(self, table: DataFrame, previous: DataFrame | None, state: dict | None):
        """
        답 못 찾은 질문 목록에서 체크박스가 바뀐 행만 save_review 로 kb_gaps 에 저장.
        previous 는 마지막으로 그린 목록(비교 기준). 저장하지 못한 행은 체크하기 전 값으로 되돌림

        Returns: DataFrame, DataFrame, str (목록, 새 비교 기준, 안내 문구)
        """
        if previous is None:   # 새로고침으로 목록을 그리기 전
            return table, table, ""
        user = _current_user(state)
        result = previous.copy()
        saved = failed = 0
        for log_id, reviewed, kb_update_needed in find_review_changes(previous, table):
            if self.save_review(log_id, reviewed, kb_update_needed, user):
                row = result["No"] == log_id
                result.loc[row, REVIEWED] = reviewed
                result.loc[row, KB_UPDATE_NEEDED] = kb_update_needed
                saved += 1
            else:
                failed += 1
        if failed:
            message = f"{failed}건을 저장하지 못했습니다. 권한과 DB 연결을 확인해 주세요."
        elif saved:
            message = f"{saved}건 저장 ({datetime.now():%H:%M:%S})"
        else:
            message = ""
        return result, result, message

    def _top_symptoms(self, logs: DataFrame) -> DataFrame:
        """
        자주 묻는 증상 Top 10: 근거 KB 가 있는 질문을 첫 번째 KB 의 증상(kb_symptoms)으로 묶어 셈.
        질문 문장은 표현이 제각각이라 그대로 묶으면 같은 증상이 흩어짐 (응답 스키마 9.2절)

        Returns: DataFrame (순위, 증상, 질문 수)
        """
        first_kb = logs["kb_ids"].map(lambda kb_ids: kb_ids[0] if isinstance(kb_ids, list) and kb_ids else None)
        symptoms = first_kb.dropna().map(lambda kb_id: self.kb_symptoms.get(kb_id, kb_id))
        counts = symptoms.value_counts().head(TOP_SYMPTOMS)
        return pd.DataFrame({
            "순위": range(1, len(counts) + 1),
            "증상": counts.index.astype(str),
            "질문 수": counts.to_numpy(),
        })


def period_range(period: str, today: date) -> tuple[date, date]:
    """
    기간 이름(PERIODS)을 load_logs 에 넘길 시작일·종료일로 변환. 종료일은 오늘

    Returns: date, date
    """
    return today - timedelta(days=PERIODS[period] - 1), today


def find_review_changes(previous: DataFrame, current: DataFrame) -> list[tuple[int, bool, bool]]:
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
        except (TypeError, ValueError):   # 화면에서 빈 행을 새로 만든 경우
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
