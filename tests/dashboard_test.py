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
from chatbot.ui.admin.quality_tab import NOT_FOUND_HEADERS, render_quality_tab  # noqa: E402
from chatbot.ui.admin.usage_tab import render_usage_tab  # noqa: E402
from chatbot.ui.dashboard import (  # noqa: E402
    ADMIN_TITLE,
    BACK_LABEL,
    AdminDashboard,
    find_review_changes,
    load_kb_symptoms,
    load_kb_symptoms_ko,
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
    assert list(table["No"]) == [5, 4]  # 최신순 (No 는 저장용, 화면에서는 숨김)
    assert list(table["날짜"]) == ["10/27 09:00", "10/25 09:00"]   # Figma 07 라이트: 날짜 · 질문 · 체크박스 2개
    assert "설비" not in table.columns
    assert bool(table.loc[0, "담당자 확인"]) and not table.loc[1, "담당자 확인"]  # kb_gaps 검토 상태


def test_not_found_list_without_search_score(db):
    """기간 안의 검색 점수가 모두 비어 있어도(top_score 가 숫자 열이 아님) 목록 · 지표가 깨지지 않음"""
    add_log(db, datetime(2026, 9, 1, 9, 0), question="점수 없음", not_found=1, top_score=None)
    dashboard = AdminDashboard()
    logs = dashboard.load_logs(date(2026, 9, 1), date(2026, 9, 1), db)
    table = dashboard.not_found_list(logs)
    assert list(table["질문"]) == ["점수 없음"]
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
    assert dashboard.back_button.value == BACK_LABEL == "뒤로가기 (채팅 화면)"   # Figma 상단 바
    assert dashboard.period_input.value == "최근 7일"                            # 운영 현황 탭 맨 위 기간 표시
    values = dashboard.refresh("최근 30일", {"user": ADMIN})
    assert len(values) == len(dashboard.refresh_outputs)
    assert len(dashboard.refresh("이상한 기간", {"user": ADMIN})) == len(values)   # 모르는 기간 → 기본 기간
    with pytest.raises(gr.Error):
        dashboard.refresh("최근 7일", {"user": ENGINEER})


# ---------------- 화면 HTML (Figma 06 · 07) ----------------
def test_render_usage_tab_cards(logs):
    stats = AdminDashboard(kb_symptoms={"TS-HYTROL138ACC-T01-04": "<b>과열</b>"}).usage_stats(logs, WEEK_START, TODAY)
    caption, daily, input_types, models, top = render_usage_tab(stats, WEEK_START, TODAY)
    assert "10/21~10/27 · 질문 7건" in caption
    assert daily.count("class='adm-bar'") == 7 and "10/27 · 5건" in daily          # 날마다 막대 1개, 건수는 막대 위
    assert "텍스트 질문" in input_types and "그 외 사진" not in input_types         # 그 외 사진은 0건이면 숨김 (Figma 3가지)
    assert "미지정(전체)" in models and "5건" in models
    assert "<b>과열</b>" not in top and "&lt;b&gt;과열" in top                       # 증상 글자는 escape


def test_render_usage_tab_escapes_model_and_shows_other_photo(db):
    add_log(db, datetime(2026, 10, 26, 9, 0), input_type="other", equipment_model="<i>CV</i>")
    dashboard = AdminDashboard()
    stats = dashboard.usage_stats(dashboard.load_logs(WEEK_START, TODAY, db), WEEK_START, TODAY)
    _, _, input_types, models, _ = render_usage_tab(stats, WEEK_START, TODAY)
    assert "그 외 사진" in input_types                                                # 있으면 4번째로 보임
    assert "<i>CV</i>" not in models and "&lt;i&gt;CV&lt;/i&gt;" in models


def test_render_usage_tab_long_period_and_no_logs(db):
    dashboard = AdminDashboard()
    month_start = TODAY - timedelta(days=29)
    stats = dashboard.usage_stats(dashboard.load_logs(month_start, TODAY, db), month_start, TODAY)
    _, daily, *_ = render_usage_tab(stats, month_start, TODAY)
    assert "is-dense" in daily and daily.count("class='adm-bar'") == 30               # 30일: 막대 위 숫자 대신 title
    empty = dashboard.usage_stats(dashboard.load_logs(date(2026, 1, 1), date(2026, 1, 7), db), TODAY, TODAY)
    caption, _, input_types, models, top = render_usage_tab(empty, TODAY, TODAY)
    assert "이 기간에는 질문이 없습니다" in caption and "이 기간에는 질문이 없습니다" in models + top + input_types


def test_render_quality_tab_kpi_cards(logs):
    dashboard = AdminDashboard()
    not_found = dashboard.not_found_list(logs)
    kpis, table, status = render_quality_tab(dashboard.quality_stats(logs), not_found, "최근 7일")
    assert kpis.count("class='adm-kpi'") == 2 and "답을 못 찾은 비율" in kpis and "28.6%" in kpis
    assert "최근 7일 · 7건 중 2건" in kpis                                              # Figma 설명 "최근 7일 · 812건 중 68건"
    assert "최근 7일 · 답변 3건 중 2건" in kpis
    assert table is not_found and status == ""


def test_admin_title_matches_figma():
    assert ADMIN_TITLE == "관리자 대시보드"



# ---------------- 10/30 다듬기: 한국어 증상 · 검토 체크 순서 · 기간 입력 막기 ----------------
def test_kb_symptoms_ko_covers_every_kb():
    """data/kb_symptoms_ko.csv 에 kb/ 의 모든 KB 한국어 증상이 있음 (새 KB 를 넣으면 이 표도 채워야 함)"""
    korean = load_kb_symptoms_ko(ROOT / "data" / "kb_symptoms_ko.csv")
    missing = set(load_kb_symptoms(ROOT / "kb")) - set(korean)
    assert not missing, f"한국어 증상이 없는 KB: {sorted(missing)}"
    assert load_kb_symptoms_ko(ROOT / "없는 파일.csv") == {}


def test_top_symptoms_korean_with_original(logs):
    dashboard = AdminDashboard(kb_symptoms={"TS-HYTROL138ACC-T01-04": "Motor or reducer overheating."},
                               kb_symptoms_ko={"TS-HYTROL138ACC-T01-04": "<모터> 과열"})
    top = dashboard.usage_stats(logs, WEEK_START, TODAY)["top_symptoms"]
    assert top.loc[0, "증상"] == "<모터> 과열" and top.loc[0, "원문"] == "Motor or reducer overheating."
    assert top.loc[1, "증상"] == "TS-FENNERBELT-T01-07" and top.loc[1, "원문"] == ""   # 한국어가 없으면 원문만
    html = render_usage_tab(dashboard.usage_stats(logs, WEEK_START, TODAY), WEEK_START, TODAY)[4]
    assert "&lt;모터&gt; 과열" in html and "adm-symptom-original'>Motor or reducer overheating." in html


def test_kb_update_needs_reviewed(db, logs):
    dashboard = AdminDashboard(db)
    previous = dashboard.not_found_list(logs)        # No 5: 담당자 확인 O, No 4: 둘 다 X
    edited = previous.copy()
    edited.loc[1, "KB 업데이트 필요"] = True           # 담당자 확인 없이 KB 업데이트 필요만 체크
    table, _, message = dashboard.on_review_input(edited, previous, {"user": ADMIN})
    assert not table.loc[1, "KB 업데이트 필요"] and "담당자 확인을 먼저" in message
    assert db.fetch("SELECT * FROM kb_gaps WHERE log_id = %s", (4,)) == []      # 저장하지 않음

    reviewed = previous.copy()
    reviewed.loc[0, "KB 업데이트 필요"] = True        # 담당자 확인한 행은 체크 가능
    table, snapshot, _ = dashboard.on_review_input(reviewed, previous, {"user": ADMIN})
    assert bool(table.loc[0, "KB 업데이트 필요"])
    unchecked = snapshot.copy()
    unchecked.loc[0, "담당자 확인"] = False            # 담당자 확인을 풀면 KB 업데이트 필요도 함께 풀림
    table, _, _ = dashboard.on_review_input(unchecked, snapshot, {"user": ADMIN})
    assert not table.loc[0, "담당자 확인"] and not table.loc[0, "KB 업데이트 필요"]
    assert db.fetch("SELECT kb_update_needed FROM kb_gaps WHERE log_id = %s", (5,))[0]["kb_update_needed"] == 0


def test_period_dropdown_is_not_typeable(db):
    dashboard = AdminDashboard(db)
    with gr.Blocks():
        dashboard.build_admin_tab(gr.State({"user": ADMIN}))
    assert dashboard.period_input.filterable is False   # 글자를 칠 수 없고 목록에서만 고름


# ---------------- 10/30 Figma Design 섹션: 시스템 탭(08) · 다크(딥 포레스트) ----------------
def test_system_tab_matches_figma_08():
    from chatbot.ui.admin.system_tab import render_period, render_response_time, render_stage_times

    assert "질문 입력 ~ 답변 완료" in render_response_time(4.5)
    assert render_period("최근 7일 (2026-10-27)") == "<p class='system-period-text'>최근 7일 (2026-10-27)</p>"
    html = render_stage_times({"질문 이해": 0.3, "하이브리드 검색": 0.4, "근거 검증": None, "답변 생성": 3.2})
    assert "--ratio: 1.000" in html and "--ratio: 0.094" in html and "--ratio: 0.000" in html   # 막대 = 시간 / 가장 긴 시간
    assert html.index("--ratio: 1.000") < html.index("3.2초")                                   # 시간 글자는 막대 바로 뒤


def test_refresh_system_period_shows_end_date(db):
    dashboard = AdminDashboard(db, KB_SYMPTOMS)
    with gr.Blocks():
        dashboard.build_admin_tab(gr.State({"user": ADMIN}))
    period_html = dashboard.refresh("최근 7일", {"user": ADMIN})[-3]   # 시스템 탭: 응답 시간 · 오류 비율 · 기간 · 단계 · 오류 표
    assert f"최근 7일 ({date.today():%Y-%m-%d})" in period_html        # Figma 08: "최근 7일 (2026-10-21)"


def test_admin_dark_colors_use_dark_class():
    """body.dark #admin-root 로 쓰면 Gradio 가 한 번 더 넣는 사본(.gradio-container… .contain #admin-root)의 라이트 색이 더 강해 다크가 안 됨"""
    css = (ROOT / "chatbot" / "ui" / "admin" / "admin.css").read_text(encoding="utf-8")
    assert ".dark #admin-root {" in css and "body.dark #admin-root" not in css


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
