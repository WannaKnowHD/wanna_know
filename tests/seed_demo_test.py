"""
seed.py 데모 로그 테스트 (build_demo_logs · DEMO_SCENARIOS, WBS 5.1.1)

- 실제 MySQL · .env 를 쓰지 않는다
- 데모 로그를 sqlite 메모리 DB(tests/sqlite_db.py)에 넣고 대시보드 함수(load_logs · usage_stats · quality_stats ·
  not_found_list)로 읽어서, 운영 현황 · 답변 품질 탭에 값이 제대로 나오는지 확인한다
실행: python -m pytest tests/seed_demo_test.py -v
"""
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # sqlite_db

from sqlite_db import SqliteDb  # noqa: E402

from chatbot.core.logger import LOG_COLUMNS  # noqa: E402
from chatbot.ui.dashboard import INPUT_TYPE_LABELS, AdminDashboard, load_kb_symptoms  # noqa: E402

# scripts/ 는 패키지가 아니라서 파일 경로로 불러온다
_spec = importlib.util.spec_from_file_location("seed", ROOT / "scripts" / "seed.py")
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)

NOW = datetime(2026, 10, 29, 15, 30)
KB_IDS = seed.load_kb_ids(ROOT / "kb")


@pytest.fixture
def logs():
    return seed.build_demo_logs(NOW, KB_IDS)


def insert(db, logs):
    """reset_demo_logs 와 같은 열로 sqlite 에 넣는다 (DELETE JOIN 은 MySQL 문법이라 넣기만 따라 함)."""
    columns = ("created_at", *LOG_COLUMNS)
    for log in logs:
        values = [json.dumps(log[c], ensure_ascii=False) if c in ("kb_ids", "latency_ms") else log[c] for c in columns]
        values[0] = f"{log['created_at']:%Y-%m-%d %H:%M:%S}"
        log_id = db.execute(f"INSERT INTO chat_logs ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                            tuple(values))
        if log["review"] is not None:
            db.execute("INSERT INTO kb_gaps (log_id, reviewed, kb_update_needed, reviewed_by) VALUES (%s, %s, %s, %s)",
                       (log_id, *log["review"], seed.DEMO_REVIEWER_ID))


# ------------------------------------------------------------------------------
# 데모 로그 내용
# ------------------------------------------------------------------------------
def test_count_and_owner(logs):
    assert len(logs) == sum(s.repeat for s in seed.DEMO_SCENARIOS)
    assert {log["employee_id"] for log in logs} == {seed.DEMO_EMPLOYEE_ID}
    assert {log["source"] for log in logs} == {"user"}          # 대시보드 통계에 들어가는 실사용 로그
    assert set(LOG_COLUMNS) <= set(logs[0])                       # logger.py 와 같은 열


def test_dates_are_recent_and_not_in_future(logs):
    first_day = NOW.date() - timedelta(days=seed.DEMO_DAYS - 1)
    assert all(first_day <= log["created_at"].date() and log["created_at"] <= NOW for log in logs)
    assert len({log["created_at"].date() for log in logs}) >= 4  # 여러 날에 나뉨 (일별 그래프용)


def test_same_day_gives_same_logs():
    assert seed.build_demo_logs(NOW, KB_IDS) == seed.build_demo_logs(NOW, KB_IDS)


def test_values_match_dashboard_expectations(logs):
    assert {log["input_type"] for log in logs} <= set(INPUT_TYPE_LABELS)   # 텍스트 · 명판 · 손상 · 그 외
    for log in logs:
        if log["not_found"]:
            assert log["kb_ids"] == [] and log["top_score"] < 0.55        # 기준값 미만이라 답 못 찾음
        if log["question_type"] == "out_of_scope":
            assert not log["not_found"]                                   # logger.py 규칙과 같게
        if log["input_type"] in ("nameplate", "other"):
            assert log["top_score"] is None and log["confidence"] is not None   # 검색 없이 사진만
        assert log["latency_ms"]["total"] == sum(v for k, v in log["latency_ms"].items() if k != "total")


def test_kb_ids_exist_in_repo():
    # 시나리오의 KB id 가 kb/ 에 없으면(이름이 바뀌면) 데모 로그의 근거가 비어 버리므로 미리 알려 준다
    missing = [s.kb_id for s in seed.DEMO_SCENARIOS if s.kb_id and s.kb_id not in KB_IDS]
    assert missing == []


def test_unknown_kb_ids_are_dropped():
    logs = seed.build_demo_logs(NOW, set())
    assert all(log["kb_ids"] == [] for log in logs)


def test_demo_account_is_valid():
    ids = [spec.employee_id for spec in seed.DEFAULT_ACCOUNTS]
    assert seed.DEMO_EMPLOYEE_ID in ids and seed.DEMO_REVIEWER_ID in ids
    seed.check_account_specs(seed.DEFAULT_ACCOUNTS)               # 계정 규칙 위반이면 SeedError


# ------------------------------------------------------------------------------
# 대시보드에서 읽어 보기
# ------------------------------------------------------------------------------
def test_dashboard_shows_demo_logs(logs):
    db = SqliteDb()
    insert(db, logs)
    board = AdminDashboard(db, load_kb_symptoms(ROOT / "kb"))
    start, end = NOW.date() - timedelta(days=6), NOW.date()
    table = board.load_logs(start, end, db)
    assert len(table) == len(logs)

    usage = board.usage_stats(table, start, end)
    assert usage["total"] == len(logs)
    assert len(usage["daily"]) == 7 and usage["daily"]["질문 수"].sum() == len(logs)
    assert (usage["input_types"]["건수"] > 0).all()               # 네 가지 입력 유형 모두 있음
    assert len(usage["models"]) >= 4                               # 설비 모델별 막대가 여러 개
    assert len(usage["top_symptoms"]) >= 5                         # 자주 묻는 증상 Top 10 이 채워짐

    quality = board.quality_stats(table)
    assert quality["not_found"] == 4 and 0 < quality["not_found_rate"] < 0.2
    assert 0.5 < quality["safety_rate"] < 1                       # 안전 문구가 빠진 답변도 조금 있음

    not_found = board.not_found_list(table)
    assert len(not_found) == 4
    assert not_found["담당자 확인"].sum() == 2 and not_found["KB 업데이트 필요"].sum() == 1
