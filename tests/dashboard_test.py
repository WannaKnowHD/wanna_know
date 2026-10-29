"""AdminDashboard 운영 현황 · 답변 품질 동작 확인 (WBS 3.8.2 · 3.8.3 · 3.8.4). MySQL 없이 sqlite 메모리 DB(tests/sqlite_db.py)로 검사함.
통계 함수는 load_logs(3.8.1)가 돌려주는 표를 그대로 받음 (load_logs 자체는 tests/load_logs_test.py)

실행 (프로젝트 루트에서):
    python -m pytest tests/dashboard_test.py -v
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import gradio as gr
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # sqlite_db

from sqlite_db import SqliteDb  # noqa: E402

from chatbot.core.schemas import User  # noqa: E402
from chatbot.ui.admin.quality_tab import NOT_FOUND_HEADERS  # noqa: E402
from chatbot.ui.dashboard import (  # noqa: E402
    AdminDashboard,
    find_review_changes,
    load_kb_symptoms,
    period_range,
)

ADMIN = User("A001", "관리자", "admin")
ENGINEER = User("U001", "사원", "user")
TODAY = date(2026, 10, 27)
WEEK_START = TODAY - timedelta(days=6)   # 최근 7일: 10/21 ~ 10/27
KB_SYMPTOMS = {
    "TS-HYTROL138ACC-T01-04": "모터 또는 감속기 과열",
    "TS-FENNERBELT-T01-07": "벨트 미끄러짐",
}


def add_log(db, when, **values):
    """chat_logs에 1행을 넣음. created_at을 직접 정할 수 있게 SQL로 넣음"""
    row = {
        "source": "user",
        "employee_id": "U001",
        "question": "질문",
        "input_type": "text",
        "kb_ids": "[]",
        "top_score": 0.8,
        "not_found": 0,
        "safety_included": 0,
        "created_at": when.strftime("%Y-%m-%d %H:%M:%S"),
        **values,
    }
    columns = ", ".join(row)
    holders = ", ".join(["%s"] * len(row))
    return db.execute(f"INSERT INTO chat_logs ({columns}) VALUES ({holders})", tuple(row.values()))


@pytest.fixture
def db():
    db = SqliteDb()
    day = datetime(2026, 10, 27, 9, 0)
    add_log(db, day, kb_ids=json.dumps(["TS-HYTROL138ACC-T01-04"]), safety_included=1, equipment_model="138-ACC")
    add_log(db, day, kb_ids=json.dumps(["TS-HYTROL138ACC-T01-04"]), safety_included=1)
    add_log(db, day - timedelta(days=1), kb_ids=json.dumps(["TS-FENNERBELT-T01-07"]))
    add_log(db, day - timedelta(days=2), question="AGV 배터리 교체 주기", not_found=1, top_score=0.31)
    add_log(db, day, question="쏠려요", not_found=1, top_score=0.41, equipment_model="138-ACC")
    add_log(db, day, input_type="nameplate", top_score=None)  # 명판 인식: 검색 안 함
    add_log(db, day, input_type="damage", error="generate/llm_timeout: 30초 초과")
    add_log(db, day, source="eval", employee_id=None)  # 평가 로그는 통계에서 뺌
    add_log(db, datetime(2026, 10, 28, 0, 0))  # 기간 다음 날 0시는 빠짐
    db.execute("INSERT INTO kb_gaps (log_id, reviewed, kb_update_needed) VALUES (%s, %s, %s)", (5, 1, 0))
    return db


@pytest.fixture
def logs(db):
    """통계 함수가 받는 표: load_logs 결과 그대로 (최근 7일, 최신순)"""
    return AdminDashboard().load_logs(WEEK_START, TODAY, db)


def test_usage_stats(logs):
    stats = AdminDashboard(kb_symptoms=KB_SYMPTOMS).usage_stats(logs, WEEK_START, TODAY)
    assert stats["total"] == 7
    daily = dict(zip(stats["daily"]["날짜"], stats["daily"]["질문 수"], strict=True))
    assert len(daily) == 7 and daily["10/27"] == 5 and daily["10/21"] == 0  # 질문 없는 날도 0
    types = stats["input_types"].set_index("입력 유형")
    assert types.loc["텍스트 질문", "건수"] == 5 and types.loc["그 외 사진", "건수"] == 0
    assert types["비율(%)"].sum() == pytest.approx(100, abs=0.2)
    models = dict(zip(stats["models"]["설비 모델"], stats["models"]["질문 수"], strict=True))
    assert models == {"미지정(전체)": 5, "138-ACC": 2}
    top = stats["top_symptoms"]
    assert list(top["증상"]) == ["모터 또는 감속기 과열", "벨트 미끄러짐"]
    assert list(top["순위"]) == [1, 2] and list(top["질문 수"]) == [2, 1]


def test_usage_stats_period_like_load_logs(logs):
    """기간은 load_logs 처럼 "YYYY-MM-DD" 글자·거꾸로 넣어도 같은 기간"""
    dashboard = AdminDashboard()
    same = dashboard.usage_stats(logs, "2026-10-27", "2026-10-21")["daily"]
    assert same.equals(dashboard.usage_stats(logs, WEEK_START, TODAY)["daily"])


def test_quality_stats(logs):
    stats = AdminDashboard().quality_stats(logs)
    assert stats["total"] == 7 and stats["not_found"] == 2
    assert stats["not_found_rate"] == pytest.approx(2 / 7)
    # 답변 = 검색했고 못 찾음·오류가 아닌 3건 (명판 인식, 오류, 못 찾음 2건은 빠짐)
    assert stats["answered"] == 3 and stats["safety_included"] == 2
    assert stats["safety_rate"] == pytest.approx(2 / 3)


def test_not_found_list(logs):
    table = AdminDashboard().not_found_list(logs)
    assert list(table.columns) == NOT_FOUND_HEADERS
    assert list(table["No"]) == [5, 4]  # 최신순
    assert table.loc[0, "설비"] == "138-ACC" and table.loc[1, "설비"] == "미지정(전체)"
    assert table.loc[0, "검색 점수"] == pytest.approx(0.41)
    assert bool(table.loc[0, "담당자 확인"]) and not table.loc[1, "담당자 확인"]  # kb_gaps 검토 상태


def test_not_found_list_without_search_score(db):
    """기간 안의 검색 점수가 모두 비어 있으면 load_logs 표의 top_score 가 숫자 열이 아님 → 빈 칸으로 표시"""
    add_log(db, datetime(2026, 9, 1, 9, 0), question="점수 없음", not_found=1, top_score=None)
    dashboard = AdminDashboard()
    logs = dashboard.load_logs(date(2026, 9, 1), date(2026, 9, 1), db)
    table = dashboard.not_found_list(logs)
    assert list(table["질문"]) == ["점수 없음"] and table["검색 점수"].isna().all()
    assert dashboard.quality_stats(logs)["answered"] == 0


def test_stats_with_no_logs(db):
    """질문이 없는 기간: load_logs 가 형 변환 없이 빈 표를 돌려줘도 통계가 깨지지 않음"""
    dashboard = AdminDashboard()
    empty = dashboard.load_logs(date(2026, 1, 1), date(2026, 1, 7), db)
    assert empty.empty
    usage = dashboard.usage_stats(empty, TODAY, TODAY)
    assert usage["total"] == 0 and list(usage["daily"]["질문 수"]) == [0]
    assert usage["models"].empty and usage["top_symptoms"].empty
    quality = dashboard.quality_stats(empty)
    assert quality["not_found_rate"] == 0.0 and quality["safety_rate"] == 0.0
    table = dashboard.not_found_list(empty)
    assert table.empty and list(table.columns) == NOT_FOUND_HEADERS


def test_save_review_inserts_then_updates(db):
    dashboard = AdminDashboard(db)
    assert dashboard.save_review(4, True, True, ADMIN)
    assert dashboard.save_review(4, True, False, ADMIN)  # 두 번째는 같은 행을 갱신
    rows = db.fetch("SELECT * FROM kb_gaps WHERE log_id = %s", (4,))
    assert len(rows) == 1 and rows[0]["kb_update_needed"] == 0
    assert rows[0]["reviewed_by"] == "A001" and rows[0]["reviewed_at"]


def test_save_review_checks_admin_and_db_errors(db):
    dashboard = AdminDashboard(db)
    assert not dashboard.save_review(4, True, True, ENGINEER)
    assert not dashboard.save_review(4, True, True, None)
    assert db.fetch("SELECT * FROM kb_gaps WHERE log_id = %s", (4,)) == []
    db.broken = True
    assert not dashboard.save_review(4, True, True, ADMIN)


def test_find_review_changes():
    before = pd.DataFrame({"No": [5, 4], "담당자 확인": [True, False], "KB 업데이트 필요": [False, False]})
    after = before.copy()
    after.loc[1, "KB 업데이트 필요"] = True
    assert find_review_changes(before, after) == [(4, False, True)]
    assert find_review_changes(before, before) == []


def test_on_review_input_saves_only_changed_rows(db, logs):
    dashboard = AdminDashboard(db)
    previous = dashboard.not_found_list(logs)
    edited = previous.copy()
    edited.loc[1, "담당자 확인"] = True
    table, snapshot, message = dashboard.on_review_input(edited, previous, {"user": ADMIN})
    assert bool(table.loc[1, "담당자 확인"]) and "1건 저장" in message
    assert db.fetch("SELECT reviewed FROM kb_gaps WHERE log_id = %s", (4,))[0]["reviewed"] == 1
    # 저장한 값은 load_logs 로 다시 읽어도 보임 (kb_gaps LEFT JOIN)
    assert list(dashboard.not_found_list(dashboard.load_logs(WEEK_START, TODAY, db))["담당자 확인"]) == [True, True]

    table, _, message = dashboard.on_review_input(edited, previous, {"user": ENGINEER})
    assert not table.loc[1, "담당자 확인"] and "저장하지 못했습니다" in message  # 되돌림


def test_period_range():
    assert period_range("오늘", TODAY) == (TODAY, TODAY)
    assert period_range("최근 7일", TODAY) == (WEEK_START, TODAY)


def test_load_kb_symptoms(tmp_path):
    (tmp_path / "troubleshooting").mkdir()
    (tmp_path / "maintenance").mkdir()
    (tmp_path / "troubleshooting" / "hytrol.json").write_text(
        json.dumps([{"kb_id": "TS-A-T01-01", "symptom": "모터 과열"}]), encoding="utf-8"
    )
    (tmp_path / "maintenance" / "hytrol.json").write_text(
        json.dumps({"kb_id": "MT-A-T02-01", "task": "체인 윤활"}), encoding="utf-8"
    )
    (tmp_path / "maintenance" / "broken.json").write_text("{", encoding="utf-8")
    assert load_kb_symptoms(tmp_path) == {"TS-A-T01-01": "모터 과열", "MT-A-T02-01": "체인 윤활"}


def test_build_admin_tab_and_refresh(db):
    dashboard = AdminDashboard(db, KB_SYMPTOMS)
    with gr.Blocks():
        state = gr.State({"user": ADMIN})
        view = dashboard.build_admin_tab(state)
    assert isinstance(view, gr.Column)
    values = dashboard.refresh("최근 30일", {"user": ADMIN})
    assert len(values) == len(dashboard.refresh_outputs)
    with pytest.raises(gr.Error):
        dashboard.refresh("최근 7일", {"user": ENGINEER})


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
