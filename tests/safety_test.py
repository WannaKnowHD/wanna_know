"""safety.py · data/safety_rules.json 동작 확인 (LLM·DB 호출 없음). 팀 결정 B안: KB 는 판단 재료만, 규칙 파일이 조건을 정함

실행 (프로젝트 루트에서):
    python -m pytest tests/safety_test.py -v
"""
import csv
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chatbot.core.safety import SafetyRules, SafetyRulesError, load_rules
from chatbot.core.schemas import SafetyNote

RULES_PATH = ROOT / "data" / "safety_rules.json"
RAW_DIR = ROOT / "data" / "raw" / "initial"


@pytest.fixture(scope="module")
def safety():
    return SafetyRules(RULES_PATH)


def answer(question_type="troubleshooting", status="answered", docs=("hytrol_138acc",)):
    return {"status": status, "question_type": question_type, "sources": [{"doc_id": d} for d in docs]}


def ids(result):
    return [note["rule_id"] for note in result["safety"]]


# ---------------------------------------------------------------- 붙이는 경우 · 안 붙이는 경우
@pytest.mark.parametrize("qtype", ["troubleshooting", "maintenance", "procedure"])
def test_maintenance_answers_always_get_safety(safety, qtype):
    assert safety.attach_safety(answer(qtype), [])["safety"]


@pytest.mark.parametrize("status,qtype", [("not_found", "troubleshooting"), ("answered", "out_of_scope"),
                                          ("nameplate", None), ("retake", None), ("error", "procedure")])
def test_no_safety_when_not_maintenance_answer(safety, status, qtype):
    assert safety.attach_safety(answer(qtype, status), [])["safety"] == []


HOT = {"kb_id": "TS-HYTROL138ACC-T01-04", "type": "troubleshooting", "symptom": "Motor or reducer overheating.",
       "causes": [{"cause": "Conveyor is overloaded.", "actions": ["Check capacity of conveyor and reduce load to recommended level."]},
                  {"cause": "Low voltage to motor.", "actions": ["Have electrician check and correct as necessary."]}]}
NOISE = {"kb_id": "TS-HYTROL138ACC-T01-03", "type": "troubleshooting", "symptom": "Loud popping or grinding noise.",
         "causes": [{"cause": "Defective bearing.", "actions": ["Replace bearing."]}]}
LUBE = {"kb_id": "MT-HYTROL138ACC-T01-01", "type": "maintenance", "task": "Lubricate drive chain",
        "details": ["non-detergent petroleum or synthetic lubricant"], "lockout_required": True}
VISUAL = {"kb_id": "MT-PRECOLSERIES-T01-01", "type": "maintenance", "task": "Inspect conveyor covers",
          "lockout_required": False}


def test_troubleshooting_gets_loto_then_qualified(safety):
    assert ids(safety.attach_safety(answer(), [NOISE])) == ["HY-LOTO", "HY-QUALIFIED"]


def test_electric_cause_adds_electrician(safety):
    """원인에 voltage·electrician 이 있으면 전기 작업 주의"""
    assert ids(safety.attach_safety(answer(), [HOT])) == ["HY-LOTO", "HY-ELECTRICIAN", "HY-QUALIFIED"]


def test_maintenance_lockout_true_gets_loto_and_lube_rule(safety):
    result = safety.attach_safety(answer("maintenance"), [LUBE])
    assert ids(result) == ["HY-LOTO", "HY-QUALIFIED", "HY-NO-LUBE-RUNNING"]


def test_maintenance_lockout_false_skips_loto(safety):
    result = safety.attach_safety(answer("maintenance", docs=("preco_l_series",)), [VISUAL])
    assert ids(result) == ["PR-QUALIFIED", "PR-NO-MAINT-RUNNING"]


def test_maintenance_without_kb_gets_qualified(safety):
    """점검 답변인데 KB 없이 청크로 답한 경우: LOTO 판단 재료가 없으니 자격자 문구"""
    assert ids(safety.attach_safety(answer("maintenance"), [])) == ["HY-QUALIFIED"]


def test_procedure_gets_guard(safety):
    assert ids(safety.attach_safety(answer("procedure"), [])) == ["HY-LOTO", "HY-QUALIFIED", "HY-GUARDS"]


def test_preco_gets_its_own_rules(safety):
    assert ids(safety.attach_safety(answer(docs=("preco_l_series",)), [])) == ["PR-POWER-OFF", "PR-QUALIFIED"]


def test_two_docs_one_rule_per_kind(safety):
    """출처가 두 문서여도 같은 종류 문구는 1개 (첫 출처 문서 것)"""
    assert ids(safety.attach_safety(answer(docs=("preco_l_series", "hytrol_138acc")), [])) == ["PR-POWER-OFF", "PR-QUALIFIED"]


@pytest.mark.parametrize("doc", ["fenner_belt", "mock_modular_belt_manual_EN"])
def test_doc_without_rules_gets_common(safety, doc):
    """Fenner·관리자가 추가한 PDF(MB-200)는 문서 규칙이 없어도 공통 규칙이 붙음"""
    assert ids(safety.attach_safety(answer(docs=(doc,)), [])) == ["COMMON-LOTO"]


def test_no_sources_gets_common(safety):
    assert ids(safety.attach_safety(answer(docs=()), [])) == ["COMMON-LOTO"]


def test_at_most_three(safety):
    for qtype in ("troubleshooting", "maintenance", "procedure"):
        assert len(safety.attach_safety(answer(qtype, docs=("hytrol_138acc", "preco_l_series")), [HOT, LUBE])["safety"]) <= 3


def test_text_and_source_are_exactly_from_rules_file(safety):
    """평가 기준: 문구·출처가 safety_rules.json 과 같아야 함 (LLM 문구 아님)"""
    rules = {r["rule_id"]: r for r in json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]}
    for note in safety.attach_safety(answer(), [HOT])["safety"]:
        rule = rules[note["rule_id"]]
        assert (note["text"], note["doc_title"], note["page"]) == (rule["text"], rule["doc_title"], rule["page"])
        SafetyNote(**note)                                            # 응답 스키마에 맞음


def test_input_answer_not_changed(safety):
    before = answer()
    copy = json.loads(json.dumps(before))
    safety.attach_safety(before, [])
    assert before == copy


# ---------------------------------------------------------------- 규칙 파일 검사
def test_rules_file_is_valid():
    rules = load_rules(RULES_PATH)
    assert any("*" in r["doc_ids"] for r in rules)


def test_every_initial_doc_is_covered():
    """sources.csv 의 문서마다 문서 규칙이나 공통 규칙이 적용됨"""
    rules = load_rules(RULES_PATH)
    with open(ROOT / "data" / "sources.csv", encoding="utf-8-sig") as f:
        doc_ids = [row["doc_id"] for row in csv.DictReader(f)]
    for doc_id in doc_ids:
        assert any(doc_id in r["doc_ids"] or "*" in r["doc_ids"] for r in rules), doc_id


def test_quotes_are_in_manuals():
    """quote 가 매뉴얼 PDF 원문에 실제로 있음 (PDF 가 없으면 건너뜀)"""
    pymupdf = pytest.importorskip("pymupdf")
    pdfs = {"hytrol_138acc": "hytrol_138acc_p1-27.pdf", "preco_l_series": "preco_l_series_p6-29_54-60.pdf"}
    if not all((RAW_DIR / f).exists() for f in pdfs.values()):
        pytest.skip("data/raw/initial 에 매뉴얼 PDF 가 없음")
    def norm(text):
        return re.sub(r"\s+", "", text.replace("-\n", ""))

    texts = {doc: norm("".join(p.get_text() for p in pymupdf.open(RAW_DIR / f))) for doc, f in pdfs.items()}
    for rule in load_rules(RULES_PATH):
        if rule["quote"]:
            assert norm(rule["quote"]) in texts[rule["doc_ids"][0]], rule["rule_id"]


def bad_rule(**change):
    """규칙 1개짜리 파일 내용. change 로 칸을 바꾸고, 값이 None 이면 그 칸을 뺀다"""
    rule = {"rule_id": "A", "kind": "loto", "doc_ids": ["*"], "when": [{"always": True}],
            "text": "t", "doc_title": None, "page": None, **change}
    return json.dumps({"rules": [{k: v for k, v in rule.items() if k not in change or change[k] is not None}]})


@pytest.mark.parametrize("content,message", [
    (None, "파일이 없습니다"),
    ("{not json", "형식이 틀렸습니다"),
    ('{"rules": []}', "규칙이 없습니다"),
    (bad_rule(doc_ids=["x"]), "공통 규칙"),
    (bad_rule(when=[{"sometimes": True}]), "when 이 틀렸습니다"),
    (bad_rule(when=None), "칸이 없습니다"),
])
def test_bad_rules_file_fails_at_start(tmp_path, content, message):
    path = tmp_path / "safety_rules.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(SafetyRulesError, match=message):
        SafetyRules(path)