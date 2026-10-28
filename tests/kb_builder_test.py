"""
KbBuilder 테스트 (to_troubleshooting_kb · to_maintenance_kb) + 저장소의 kb/*.json 확인

- 표 행은 매뉴얼에서 옮긴 것을 직접 적어서 확인한다 (매뉴얼 PDF 없이도 돌아감)
- kb/ 의 json 은 scripts/build_kb.py 로 만든 결과. 스키마·id 규칙에 맞는지 확인한다
실행: python -m pytest tests/kb_builder_test.py -v
"""
import glob
import json
import os

import pytest
from pydantic import TypeAdapter, ValidationError

from chatbot.core.schemas import KbEntry, MaintenanceKB, Section
from chatbot.rag.document_processor import KbBuilder

MODELS = os.path.join("data", "models.csv")
HYTROL_MODELS = ["138-ACC", "138-LRC", "138-LRS", "138-LRSS"]


@pytest.fixture
def kb():
    return KbBuilder(MODELS)


# ------------------------------------------------------------------------------
# to_troubleshooting_kb
# ------------------------------------------------------------------------------
def test_numbered_causes_paired(kb):
    # Hytrol: 원인 n) 과 조치 n) 이 짝
    rows = [["Conveyor will not start or motor quits frequently.",
             "1) Motor is overloaded. 2) Motor is drawing too much current.",
             "1) Check for overloading of conveyor. 2) Check heater or circuit breaker and change if necessary."]]
    (item,) = kb.to_troubleshooting_kb(rows, "hytrol_138acc", 22, 1, "TROUBLE SHOOTING DRIVES")
    assert item["kb_id"] == "TS-HYTROL138ACC-T01-01"
    assert item["applies_to"] == HYTROL_MODELS
    assert item["sources"] == [{"doc_id": "hytrol_138acc", "page": 22, "ref": "TROUBLE SHOOTING DRIVES"}]
    assert [(c["cause_id"], c["cause"], c["actions"]) for c in item["causes"]] == [
        ("TS-HYTROL138ACC-T01-01#c1", "Motor is overloaded.", ["Check for overloading of conveyor."]),
        ("TS-HYTROL138ACC-T01-01#c2", "Motor is drawing too much current.",
         ["Check heater or circuit breaker and change if necessary."]),
    ]


def test_merged_cells_inherit(kb):
    # PRECO: 병합 칸이라 빈 증상·조치는 위 행 값. 증상 1개 = KB 1건, 표 안 순번은 01, 02 …
    rows = [["Excessive noise", "Belt is too loose", "Tension the belt"],
            ["Belt steers off", "Incorrectly adjusted carrying idlers.", "Adjust idlers and/or pulleys."],
            ["", "Incorrectly adjusted reverse idlers.", ""]]
    noise, steer = kb.to_troubleshooting_kb(rows, "preco_l_series", 57, 4, "TROUBLESHOOTING")
    assert [noise["kb_id"], steer["kb_id"]] == ["TS-PRECOLSERIES-T04-01", "TS-PRECOLSERIES-T04-02"]
    assert steer["symptom"] == "Belt steers off"
    assert [c["actions"] for c in steer["causes"]] == [["Adjust idlers and/or pulleys."]] * 2
    assert steer["causes"][1]["cause_id"] == "TS-PRECOLSERIES-T04-02#c2"


def test_numbered_steps_for_one_cause(kb):
    # 원인 1개에 번호 붙은 조치 = 조치 단계 여러 개. "(13. page)" 같은 글 중간 숫자는 나누지 않는다
    rows = [["Motor does not turn on", "Thermal overload tripped",
             "1. Check why the conveyor is overloaded; 2. If necessary remove excessive material from conveyor;"],
            ["Belt steers off", "Incorrectly adjusted carrying idlers.",
             "Adjust idlers and/or pulleys. See belt alignment section (13. page)."]]
    motor, steer = kb.to_troubleshooting_kb(rows, "preco_l_series", 57, 4, "TROUBLESHOOTING")
    assert motor["causes"][0]["actions"] == ["Check why the conveyor is overloaded",
                                             "If necessary remove excessive material from conveyor"]
    assert steer["causes"][0]["actions"] == ["Adjust idlers and/or pulleys. See belt alignment section (13. page)."]


def test_action_list_and_all_models(kb):
    # Fenner: 조치를 이미 나눈 목록으로 넘김. models.csv 에 없는 공통 매뉴얼은 "*"
    rows = [["Belt slips.", "Pulley lagging worn", ["replace pulley lagging."]],
            ["", "Pulleys too small", ["use larger diameter pulleys."]]]
    (item,) = kb.to_troubleshooting_kb(rows, "fenner_belt", 27, 1, "TROUBLE SHOOTING")
    assert item["kb_id"] == "TS-FENNERBELT-T01-01"
    assert item["applies_to"] == ["*"]
    assert [c["cause"] for c in item["causes"]] == ["Pulley lagging worn", "Pulleys too small"]


@pytest.mark.parametrize("rows, message", [
    ([["", "Belt too loose", "Tension the belt"]], "첫 행의 증상이 비어 있음"),
    ([["Belt slips", "", "Tension the belt"]], "원인이 비어 있음"),
    ([["Belt slips", "Belt too loose", "Tension the belt"],
      ["Motor hot", "Overloaded", ""]], "조치가 비어 있음"),              # 다른 증상의 조치는 이어받지 않음
    ([["Belt slips", "1) A. 2) B.", "1) Fix A."]], "번호가 맞지 않음"),   # 원인 2개, 조치 1개
])
def test_troubleshooting_bad_rows(kb, rows, message):
    with pytest.raises(ValueError, match=message):
        kb.to_troubleshooting_kb(rows, "hytrol_138acc", 22, 1, "T")


# ------------------------------------------------------------------------------
# to_maintenance_kb
# ------------------------------------------------------------------------------
def test_maintenance_intervals(kb):
    rows = [
        {"task": "MOTOR: Check Noise", "interval": "Monthly", "lockout_required": False},
        {"task": "Inspect belt rollers for wear", "interval": "MONTHLY"},
        {"task": "Lubricate the bearings", "interval": "1500 to 3000 Operating hours", "interval_note": "6 to 12 months"},
        {"task": "Change the oil", "interval": "every 10000 operating hours, at least every 2 years"},
        {"task": "General overhaul", "interval": "At least every 10 years"},
    ]
    items = kb.to_maintenance_kb(rows, "preco_l_series", 55, 3, "Gear motor maintenance")
    assert [i["kb_id"] for i in items] == [f"MT-PRECOLSERIES-T03-0{n}" for n in range(1, 6)]
    noise, rollers, bearing, oil, overhaul = items
    assert (noise["interval"], noise["lockout_required"]) == ("monthly", False)
    assert (rollers["interval"], rollers["lockout_required"]) == ("monthly", True)   # 기본은 잠금 필요
    assert (bearing["interval"], bearing["interval_hours"], bearing["interval_hours_max"],
            bearing["interval_note"]) == ("operating_hours", 1500, 3000, "6 to 12 months")
    # 시간 말고 다른 조건이 있으면 원문 전체를 보충으로 남긴다
    assert (oil["interval_hours"], oil["interval_note"]) == (10000, "every 10000 operating hours, at least every 2 years")
    assert "interval_hours_max" not in oil
    # daily~yearly·운전 시간으로 못 나타내는 주기는 other + 원문
    assert (overhaul["interval"], overhaul["interval_note"]) == ("other", "At least every 10 years")


def test_maintenance_unknown_key(kb):
    with pytest.raises(ValueError, match="모르는 키"):
        kb.to_maintenance_kb([{"task": "Lubricate", "interval": "Weekly", "lockout": False}],
                             "hytrol_138acc", 24, 3, "Checklist")


def test_schema_other_needs_note():
    # interval 이 other 인데 원문 주기가 없으면 무슨 주기인지 알 수 없으므로 오류
    with pytest.raises(ValidationError, match="interval_note"):
        MaintenanceKB(type="maintenance", kb_id="MT-X-T01-01", applies_to=["*"],
                      sources=[{"doc_id": "x", "page": 1, "ref": "r"}], task="General overhaul",
                      interval="other", lockout_required=True)


# ------------------------------------------------------------------------------
# from_sections (관리자가 추가한 새 PDF 자동 변환)
# ------------------------------------------------------------------------------
TROUBLE_TABLE = ("TROUBLE SHOOTING DRIVES\n"
                 "| TROUBLE | CAUSE | SOLUTION |\n|---|---|---|\n"
                 "| Motor overheating. | 1) Motor is overloaded. 2) Low voltage. | 1) Reduce the load. 2) Check voltage. |")
CHECKLIST_TABLE = ("| COMPONENT | SUGGESTED ACTION | SCHEDULE |  |\n|---|---|---|---|\n"
                   "|  |  | Weekly | Monthly |\n"
                   "| MOTOR | Check Noise |  | X |\n"
                   "|  | Check Temperature | X |  |")


def test_from_sections(kb):
    sections = [
        Section("new_doc", "Intro", 3, "text", "Read this manual."),
        Section("new_doc", "Specs", 4, "table", "| Model | Speed |\n|---|---|\n| A-1 | 60 fpm |"),   # KB 아님
        Section("new_doc", "Trouble Shooting", 22, "table", TROUBLE_TABLE),
        Section("new_doc", "Preventive Maintenance", 24, "table", CHECKLIST_TABLE),
    ]
    trouble, noise, temp = kb.from_sections(sections)
    # 표 번호는 KB로 바뀐 표만 센다 (사양표는 빠짐)
    assert trouble["kb_id"] == "TS-NEWDOC-T01-01"
    assert [noise["kb_id"], temp["kb_id"]] == ["MT-NEWDOC-T02-01", "MT-NEWDOC-T02-02"]
    # ref: 표 제목 → 없으면 절 제목
    assert trouble["sources"] == [{"doc_id": "new_doc", "page": 22, "ref": "TROUBLE SHOOTING DRIVES"}]
    assert noise["sources"][0]["ref"] == "Preventive Maintenance"
    assert [c["cause"] for c in trouble["causes"]] == ["Motor is overloaded.", "Low voltage."]
    # 병합 칸 부품 이어받기, 주기 = X 표시 칸, 잠금은 모두 True
    assert (temp["task"], temp["interval"], temp["lockout_required"]) == ("MOTOR: Check Temperature", "weekly", True)
    # models.csv 에 없는 새 문서는 "*"
    assert trouble["applies_to"] == ["*"]


def test_from_sections_hours_column_and_note(kb):
    # "Every 2,000 h" 도 주기 칸, 오른쪽 Note 칸은 details 로
    table = ("| Task | Monthly | Every 2,000 h | Note |\n|---|---|---|---|\n"
             "| Check wearstrip thickness | X |  | Replace at 3 mm wear |\n"
             "| Change gear motor oil |  | X | First change after 500 h |")
    wear, oil = kb.from_sections([Section("new_doc", "5. Maintenance", 6, "table", table)])
    assert (wear["interval"], wear["details"]) == ("monthly", ["Note: Replace at 3 mm wear"])
    assert (oil["interval"], oil["interval_hours"]) == ("operating_hours", 2000)
    assert "interval_note" not in oil                     # "Every" 말고 다른 조건이 없으므로
    assert oil["details"] == ["Note: First change after 500 h"]


def test_from_sections_caption_table_number(kb):
    # 표 제목이 표 아래에 붙는 매뉴얼: 연달아 있는 두 표 사이의 "Table 8. …" 는 위 표(트러블슈팅) 것
    sections = [
        Section("new_doc", "6. Troubleshooting", 7, "text", "DANGER: Lock out the conveyor."),
        Section("new_doc", "6. Troubleshooting", 7, "table", TROUBLE_TABLE.split("\n", 1)[1]),  # 대문자 제목 없이
        Section("new_doc", "6. Troubleshooting", 7, "text", "Table 8. Troubleshooting chart 6.1 VFD alarm codes"),
        Section("new_doc", "6. Troubleshooting", 7, "table", "| Code | Meaning | Action |\n|---|---|---|\n| E01 | Overcurrent | Restart. |"),
        Section("new_doc", "6. Troubleshooting", 7, "text", "Table 9. VFD alarm codes"),
    ]
    assert kb._captions(sections) == {1: "Table 8", 3: "Table 9"}
    (item,) = kb.from_sections(sections)
    assert item["sources"][0]["ref"] == "Table 8"


def test_from_sections_ref_falls_back_to_header(kb):
    # 표 제목도 절 제목도 없으면 머리글 행
    table = "| PROBLEM | POTENTIAL CAUSE | SOLUTION |\n|---|---|---|\n| Belt slips | Belt too loose | Tension the belt |"
    (item,) = kb.from_sections([Section("new_doc", "", 9, "table", table)])
    assert item["sources"][0]["ref"] == "PROBLEM / POTENTIAL CAUSE / SOLUTION"


def test_from_sections_skips_broken_table(kb, capsys):
    # 원인 2개·조치 1개로 짝이 안 맞는 표는 건너뛰고(경고), 다음 표는 계속 변환. 표 번호도 건너뛴 표를 세지 않음
    broken = "| TROUBLE | CAUSE | SOLUTION |\n|---|---|---|\n| Noise. | 1) A. 2) B. | 1) Fix A. |"
    sections = [Section("new_doc", "T", 1, "table", broken), Section("new_doc", "T", 2, "table", TROUBLE_TABLE)]
    (item,) = kb.from_sections(sections)
    assert item["kb_id"] == "TS-NEWDOC-T01-01" and item["sources"][0]["page"] == 2
    assert "KB 변환 건너뜀" in capsys.readouterr().out


# ------------------------------------------------------------------------------
# 저장소의 kb/*.json (scripts/build_kb.py 결과)
# ------------------------------------------------------------------------------
KB_FILES = sorted(glob.glob(os.path.join("kb", "*", "*.json")))


@pytest.mark.skipif(not KB_FILES, reason="kb/*.json 없음 (python scripts/build_kb.py 로 생성)")
def test_repo_kb_files():
    adapter = TypeAdapter(KbEntry)
    ids = []
    for path in KB_FILES:
        with open(path, encoding="utf-8") as f:
            item = adapter.validate_python(json.load(f))
        folder = os.path.basename(os.path.dirname(path))
        assert folder == item.type, path                                    # 종류별 폴더
        assert os.path.basename(path) == f"{item.kb_id}.json", path         # 파일 이름 = kb_id
        ids.append(item.kb_id)
    assert len(ids) == len(set(ids))
