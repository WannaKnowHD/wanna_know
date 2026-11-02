"""
==============================================================================
 scripts/build_kb.py : 매뉴얼 3종의 트러블슈팅 표·점검표 → kb/*.json  (담당: 김민재 / WBS 3.3.4 / 기능 D-02)
==============================================================================

[이 파일이 하는 일]
  1) PdfExtractor.extract_text 로 표를 읽는다. (crop_regions 의 비전 모델 결과는 쓰지 않음:
     모델이 다시 쓴 글자라 원문과 달라질 수 있는데, KB 문장은 원문 그대로여야 하기 때문)
  2) 표 모양이 매뉴얼마다 달라서, 매뉴얼마다 표 찾기·행 정리 함수(hytrol·preco·fenner)가 따로 있다.
     정리한 행을 KbBuilder.to_troubleshooting_kb / to_maintenance_kb 에 넘긴다.
  3) 같은 매뉴얼 안에서 내용이 완전히 같은 KB 는 1건으로 합친다. (KbBuilder.merge_applies_to)
     저장 전에 모든 KB를 확인한다. (KbBuilder.validate_kb · _validate_all)
       - KbEntry 스키마, kb_id 중복, related_kb, applies_to 가 models.csv 모델명인지
       - 증상·원인·조치·작업·보충 문장이 원본 PDF 그 쪽에 글자 그대로 있는지
  4) kb/troubleshooting/, kb/maintenance/ 의 기존 json 을 지우고 {kb_id}.json 으로 새로 쓴다.
     (표에서 빠진 KB 파일이 남아 검색되지 않게)

[표 번호 T{nn}]  매뉴얼마다 KB로 만드는 표·목록만 문서 순서대로 센다.
  Hytrol  T01 TROUBLE SHOOTING DRIVES · T02 TROUBLE SHOOTING DRIVE BELT TRACKING · T03 Preventive Maintenance Checklist
  PRECO   T01 일·주·월 점검 목록 · T02 베어링 윤활 주기 · T03 감속기 점검 주기 · T04 TROUBLESHOOTING
  Fenner  T01 TROUBLE SHOOTING (증상 A~X) + 답 번호 목록 1~53

[실행]  프로젝트 최상단 폴더에서, 가상환경을 켠 상태로
  python scripts/build_kb.py
  (data/raw/initial/ 에 매뉴얼 PDF 3개가 있어야 함)
==============================================================================
"""
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymupdf                                                  # noqa: E402

from chatbot.rag.document_processor import KbBuilder, PdfExtractor  # noqa: E402

RAW = PROJECT_ROOT / "data" / "raw" / "initial"
KB_DIR = PROJECT_ROOT / "kb"

# Hytrol 점검표에서 전원을 끄지 않고 하는 점검. 소음·온도는 돌아가는 상태에서만 확인할 수 있다.
RUNNING_CHECKS = {"Check Noise", "Check Temperature"}


# ------------------------------------------------------------------------------
# 공통
# ------------------------------------------------------------------------------
def read_pages(ex, file_name, pages):
    """{PDF 파일 쪽: Page}"""
    return {p.page_no: p for p in ex.extract_text(str(RAW / file_name), pages)}


def table_rows(region_text):
    """extract_text 의 Markdown 표 → 칸 목록의 목록 (구분 줄 |---| 은 뺌).
    칸 안의 "|" 는 rows_to_markdown 이 "/" 로 바꿔 두므로 "|" 로 나누면 칸이 그대로 나온다."""
    rows = []
    for line in region_text.splitlines():
        line = line.strip()
        if line.startswith("|") and not line.startswith("|---"):
            rows.append([c.strip() for c in line[1:-1].split("|")])
    return rows


def tables(page, first_cell):
    """그 쪽의 표 중 첫 칸이 first_cell 인 표들을 위에서 아래 순서로"""
    found = [r for r in page.regions if r["type"] == "table"]
    found.sort(key=lambda r: r["bbox"][1])
    rows = [table_rows(r["text"]) for r in found]
    return [t for t in rows if t and t[0][0] == first_cell]


# ------------------------------------------------------------------------------
# Hytrol 138-ACC
# ------------------------------------------------------------------------------
def hytrol(ex, kb):
    doc_id = "hytrol_138acc"
    pages = read_pages(ex, "hytrol_138acc_p1-27.pdf", [22, 24])

    # p22 표 2개: 머리글 TROUBLE | CAUSE | SOLUTION. 원인·조치 칸의 "1) 2)" 짝짓기는 KbBuilder 가 한다
    drives, tracking = tables(pages[22], "TROUBLE")
    page = ex.to_original_page(doc_id, 22)
    kbs = kb.to_troubleshooting_kb(drives[1:], doc_id, page, 1, "TROUBLE SHOOTING DRIVES")
    kbs += kb.to_troubleshooting_kb(tracking[1:], doc_id, page, 2, "TROUBLE SHOOTING DRIVE BELT TRACKING")

    # p24 점검표: COMPONENT | SUGGESTED ACTION | Weekly | Monthly | Quarterly (해당 주기 칸이 "X")
    (checklist,) = tables(pages[24], "COMPONENT")
    schedule = checklist[1][2:]                # 둘째 머리글 행 ["Weekly", "Monthly", "Quarterly"]
    rows, component = [], ""
    for comp, action, *marks in checklist[2:]:
        component = comp or component          # 부품 칸이 병합 칸이면 위 행 부품
        intervals = [h for h, m in zip(schedule, marks) if m == "X"]
        if len(intervals) != 1:
            raise ValueError(f"Hytrol 점검표 {component} {action}: 주기 칸이 {len(intervals)}개")
        # "Check Noise" 만으로는 무엇의 소음인지 모르므로 부품을 붙인다 (두 부분 모두 원문 그대로)
        rows.append({"task": f"{component}: {action}", "interval": intervals[0],
                     "lockout_required": action not in RUNNING_CHECKS})
    kbs += kb.to_maintenance_kb(rows, doc_id, ex.to_original_page(doc_id, 24), 3,
                                "Preventive Maintenance Checklist")
    return kbs


# ------------------------------------------------------------------------------
# PRECO L 시리즈
# ------------------------------------------------------------------------------
def preco(ex, kb):
    doc_id = "preco_l_series"
    pages = read_pages(ex, "preco_l_series_p6-29_54-60.pdf", [25, 26, 28])
    kbs = []

    # T01 p25 본문의 DAILY / WEEKLY / MONTHLY MAINTENANCE 글머리 목록 (표가 아니라 글)
    #   "•" 만 있는 줄 다음 줄부터가 항목 1개. 항목이 여러 줄이면 이어 붙인다.
    rows, interval = [], None
    for line in pages[25].text.splitlines():
        line = line.strip()
        m = re.fullmatch(r"(DAILY|WEEKLY|MONTHLY) MAINTENANCE:", line)
        if m:
            interval = m.group(1)
        elif line == "Bearing lubrication":    # 목록 끝
            break
        elif interval and line == "•":
            rows.append({"task": "", "interval": interval})
        elif interval and rows:
            rows[-1]["task"] = (rows[-1]["task"] + " " + line).strip()
    for row in rows:
        row["task"] = row["task"].rstrip(";")  # 목록 구분용 ";" 만 뺀다
    kbs += kb.to_maintenance_kb(rows, doc_id, ex.to_original_page(doc_id, 25), 1, "MAINTENANCE")

    # T02 p25 베어링 윤활 주기 표
    #   Type | Code | Environmental conditions | Operating temperature, °C | Operating hours | Time interval
    (bearing,) = [t for t in tables(pages[25], "Type") if "Lubrication intervals" in t[0]]
    head = bearing[0][:4] + bearing[1][4:]     # 머리글 2줄 → 칸 이름 1줄
    rows = []
    for cells in bearing[2:]:
        type_, code, env, temp, hours, time = cells
        rows.append({
            "task": "Lubricate the bearings",  # p25 본문 "Lubricate the bearings at the intervals shown in the table."
            "interval": f"{hours} {head[4]}",  # "1500 to 3000 Operating hours" → operating_hours 1500~3000
            "interval_note": time,             # "6 to 12 months"
            # 같은 작업 4건이 무엇이 다른지(환경·온도) 보이게 칸 이름과 값을 함께 적는다
            "details": [f"{h}: {v}" for h, v in zip(head[:4], (type_, code, env, temp))]})
    kbs += kb.to_maintenance_kb(rows, doc_id, ex.to_original_page(doc_id, 25), 2, "Bearing lubrication")

    # T03 p26 감속기(gear motor) 점검 주기 표: 주기 | "• 작업 • 작업 …" → 작업 1개 = KB 1건
    (gear,) = tables(pages[26], "Inspection and maintenance intervals")
    rows = [{"task": task.strip(), "interval": when}
            for when, works in gear[1:] for task in works.split("•") if task.strip()]
    kbs += kb.to_maintenance_kb(rows, doc_id, ex.to_original_page(doc_id, 26), 3, "Gear motor maintenance")

    # T04 p28 PROBLEM | POTENTIAL CAUSE | SOLUTION (병합 칸 이어받기는 KbBuilder 가 한다)
    (trouble,) = tables(pages[28], "PROBLEM")
    kbs += kb.to_troubleshooting_kb(trouble[1:], doc_id, ex.to_original_page(doc_id, 28), 4, "TROUBLESHOOTING")
    return kbs


# ------------------------------------------------------------------------------
# Fenner Dunlop 벨트 매뉴얼
# ------------------------------------------------------------------------------
FENNER_ANSWERS_REF = "CONVEYOR SYSTEM PROBLEMS/CAUSES AND THEIR SOLUTIONS"


def fenner_answers(pages):
    """p5-7 번호 목록 → {번호: (원인, [조치, ...], PDF 쪽)}

    "1." 만 있는 줄 다음에 글이 오기도 하고("1.\\nIdlers …"), "10. Material …" 처럼 한 줄에 오기도 한다.
    1, 2, 3 … 차례로 이어지는 번호만 새 항목으로 본다 (글 중간 줄이 숫자로 시작해도 잘못 나누지 않게).
    """
    items, expected = {}, 1
    for page_no in sorted(pages):
        for line in pages[page_no].text.splitlines():
            line = line.strip()
            m = re.match(r"(\d+)\.?(?:\s+|$)(.*)", line)
            if m and int(m.group(1)) == expected:
                items[expected] = [m.group(2), page_no]
                expected += 1
            elif items and line:
                items[expected - 1][0] = (items[expected - 1][0] + " " + line).strip()

    answers = {}
    for no, (text, page_no) in items.items():
        # "원인: 조치; 조치". 16·42번은 ":" 대신 ";" 로 적혀 있어서 ":" 가 없으면 첫 ";" 에서 나눈다
        sep = ":" if ":" in text else ";"
        cause, _, solution = text.partition(sep)
        actions = [a.strip() for a in solution.split(";") if a.strip()]
        if not cause.strip() or not actions:
            raise ValueError(f"Fenner 답 {no}번을 원인·조치로 나눌 수 없음: {text}")
        answers[no] = (cause.strip(), actions, page_no)
    return answers


def fenner(ex, kb):
    doc_id = "fenner_belt"
    pages = read_pages(ex, "Fenner-Dunlop-Conveyor-Belt-Manual-21-39.pdf", [4, 5, 6, 7])
    answers = fenner_answers({n: pages[n] for n in (5, 6, 7)})

    # p4 표: "A. Belt runs off at tail pulley. | 39 | 10 | 1 | …" (원인은 답 번호로만 적혀 있음)
    #   머리글 행이 없는 표라 첫 행도 데이터. "O" 행은 PDF 에 숫자 "0" 으로 적혀 있다.
    (matrix,) = [t for r in pages[4].regions if r["type"] == "table" for t in [table_rows(r["text"])]]
    rows, used_pages = [], []
    for label, *numbers in matrix:
        symptom = re.sub(r"^[A-Z0]\.?\s+", "", label)   # 앞의 "A. " 기호만 뺀다
        numbers = [int(n) for n in numbers if n]
        used_pages.append(sorted({answers[n][2] for n in numbers}))
        for i, n in enumerate(numbers):
            cause, actions, _ = answers[n]
            rows.append([symptom if i == 0 else "", cause, actions])
    kbs = kb.to_troubleshooting_kb(rows, doc_id, ex.to_original_page(doc_id, 4), 1, "TROUBLE SHOOTING")

    # 원인·조치 문장이 있는 답 목록 쪽도 출처로 남긴다 (증상 1개 = KB 1건이라 표 행 순서와 같음)
    for kb_dict, page_nos in zip(kbs, used_pages, strict=True):
        for page_no in page_nos:
            kb_dict["sources"].append({"doc_id": doc_id, "page": ex.to_original_page(doc_id, page_no),
                                       "ref": FENNER_ANSWERS_REF})
    return kbs


# ------------------------------------------------------------------------------
# 확인·저장
# ------------------------------------------------------------------------------
def page_texts(ex, files):
    """{(doc_id, 원본 쪽): 그 쪽 글자} — validate_kb 의 원문 대조용.

    extract_text 결과가 아니라 PyMuPDF 로 쪽 글자를 따로 읽는다 (추출 단계의 실수도 잡으려고).
    ex 는 extract_text 를 이미 실행한 PdfExtractor (그때 읽어 둔 page_map 으로 원본 쪽을 맞춘다).
    """
    texts = {}
    for doc_id, file_name in files.items():
        with pymupdf.open(str(RAW / file_name)) as pdf:
            for i, page in enumerate(pdf, start=1):
                texts[(doc_id, ex.to_original_page(doc_id, i))] = page.get_text()
    return texts


def save(kbs):
    for kind in ("troubleshooting", "maintenance"):
        folder = KB_DIR / kind
        for old in folder.glob("*.json"):
            old.unlink()
        for kb in kbs:
            if kb["type"] == kind:
                path = folder / f"{kb['kb_id']}.json"
                path.write_text(json.dumps(kb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ex = PdfExtractor(str(PROJECT_ROOT / "data" / "sources.csv"))
    kb = KbBuilder(str(PROJECT_ROOT / "data" / "models.csv"))
    # 같은 매뉴얼 안에서 내용이 완전히 같은 KB(모델마다 반복된 표)는 1건으로 합친다. 지금 3종에는 없어서 82건 그대로
    kbs = kb.merge_applies_to(hytrol(ex, kb) + preco(ex, kb) + fenner(ex, kb))

    # KB마다 검사(스키마·원문 대조·적용 모델) + 목록 전체 검사(kb_id 중복·related_kb)
    texts = page_texts(ex, {
        "hytrol_138acc": "hytrol_138acc_p1-27.pdf",
        "preco_l_series": "preco_l_series_p6-29_54-60.pdf",
        "fenner_belt": "Fenner-Dunlop-Conveyor-Belt-Manual-21-39.pdf",
    })
    errors = kb._validate_all(kbs)
    for item in kbs:
        source = "\n".join(texts[(s["doc_id"], s["page"])] for s in item["sources"])
        errors += kb.validate_kb(item, source)[1]
    if errors:
        sys.exit("[실패] KB 검사:\n  " + "\n  ".join(errors))

    save(kbs)
    for kind in ("troubleshooting", "maintenance"):
        print(f"[OK] kb/{kind}/ {sum(k['type'] == kind for k in kbs)}건")


if __name__ == "__main__":
    main()
