"""
KB 현황(화면 08) 테스트 — kb_files.py · AdminDashboard.kb_status · add_pdf (WBS 3.8.6 · 4.2.3)

- 실제 MySQL · 색인 · OpenAI · .env 를 쓰지 않는다
  · DB: sqlite 메모리 DB 의 documents 테이블 (schema.sql 과 열이 같은지도 검사)
  · 색인: 가짜 함수 (BM25 항목 파일만 만들거나 일부러 실패)
  · PDF: PyMuPDF 로 테스트용 PDF 를 그 자리에서 만든다
실행: python -m pytest tests/kb_status_test.py -v
"""
import pickle
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlite_db import schema_sql_columns  # noqa: E402

from chatbot.core.schemas import User  # noqa: E402
from chatbot.ui.admin import kb_files  # noqa: E402
from chatbot.ui.admin.kb_files import (  # noqa: E402
    KB_STATUS_COLUMNS, KbIndexer, PdfCheckError, check_pdf, load_documents, safe_filename,
)
from chatbot.ui.admin.kb_tab import render_kb_header, render_kb_table  # noqa: E402
from chatbot.ui.dashboard import AdminDashboard  # noqa: E402

ADMIN = User(employee_id="A0001", name="관리자", role="admin")
WORKER = User(employee_id="E0001", name="사원", role="user")
LONG_TEXT = "Check the drive chain tension and lubricate it every 2,080 hours. " * 10

DOCUMENTS_DDL = """CREATE TABLE documents (
  doc_id TEXT PRIMARY KEY, filename TEXT NOT NULL, title TEXT,
  source TEXT NOT NULL CHECK (source IN ('initial', 'admin_add')),
  pages INTEGER, chunks INTEGER,
  status TEXT NOT NULL DEFAULT 'indexing' CHECK (status IN ('indexing', 'done', 'failed')),
  error_message TEXT, indexed_at TIMESTAMP)"""


class DocumentsDb:
    """documents 테이블만 있는 sqlite 메모리 DB. Database 와 같은 fetch · execute 를 가진다."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(DOCUMENTS_DDL)

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("%s", "?"), params).rowcount

    def fetch(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql.replace("%s", "?"), params)]

    def rows(self):
        return {row["doc_id"]: row for row in self.fetch("SELECT * FROM documents")}


def make_pdf(text=LONG_TEXT, pages=1, password=None) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text)
    options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "owner_pw": password, "user_pw": password} if password else {}
    data = doc.tobytes(**options)
    doc.close()
    return data


@pytest.fixture
def env(tmp_path):
    """data/raw/initial · admin_add · sources.csv · 색인 위치를 tmp_path 에 만든다."""
    raw = tmp_path / "data" / "raw"
    (raw / "initial").mkdir(parents=True)
    (raw / "admin_add").mkdir()
    (raw / "initial" / "hytrol_138acc_p1-27.pdf").write_bytes(make_pdf(pages=2))
    (tmp_path / "data" / "sources.csv").write_text(
        "doc_id,file,original_file,maker\nhytrol_138acc,hytrol_138acc_p1-27.pdf,2007_583_138acc.pdf,Hytrol\n",
        encoding="utf-8")
    settings = SimpleNamespace(raw_dir=raw, admin_add_dir=raw / "admin_add", interim_dir=tmp_path / "interim",
                               chroma_dir=tmp_path / "index", bm25_path=tmp_path / "index" / "bm25.pkl")
    return settings


def fake_index(chunks_per_doc):
    """BM25 항목 파일만 만드는 가짜 색인 함수. {doc_id: 청크 수} 만큼 항목을 쓴다 (KB 항목 1건씩 함께)."""
    def build(settings):
        items = []
        for doc_id, n in chunks_per_doc.items():
            items += [{"id": f"{doc_id}:c{i}", "text": "x", "metadata": {"doc_id": doc_id, "chunk_type": "text"}}
                      for i in range(n)]
            items.append({"id": f"KB-{doc_id}", "text": "x", "metadata": {"doc_id": doc_id, "chunk_type": "kb"}})
        settings.bm25_path.parent.mkdir(parents=True, exist_ok=True)
        settings.bm25_path.write_bytes(pickle.dumps(items))
    return build


def failing_index(settings):
    raise RuntimeError("vision API timeout")


# ------------------------------------------------------------------------------
# 업로드 파일 검사 (WBS 4.2.3)
# ------------------------------------------------------------------------------
def test_good_pdf_passes():
    assert check_pdf(make_pdf(pages=3), "manual.pdf") == ("manual.pdf", 3)


@pytest.mark.parametrize("data, name, word", [
    (b"hello world", "notes.txt", "PDF 파일만"),                 # 확장자가 PDF 아님
    (b"hello world", "fake.pdf", "PDF 파일이 아닙니다"),         # 확장자만 .pdf
    (b"", "empty.pdf", "빈 파일"),
    (b"%PDF-1.7\n broken body", "broken.pdf", "깨져"),           # 깨진 PDF
    (None, "secret.pdf", "암호"),                                # 암호 PDF (아래에서 만듦)
    (None, "scan.pdf", "스캔본"),                                # 글자 없는 PDF (아래에서 만듦)
])
def test_bad_files_rejected(data, name, word):
    if name == "secret.pdf":
        data = make_pdf(password="1234")
    elif name == "scan.pdf":
        data = make_pdf(text="")
    with pytest.raises(PdfCheckError, match=word):
        check_pdf(data, name)


def test_too_big_rejected(monkeypatch):
    monkeypatch.setattr(kb_files, "MAX_PDF_BYTES", 100)
    with pytest.raises(PdfCheckError, match="너무 큽니다"):
        check_pdf(make_pdf(), "big.pdf")


@pytest.mark.parametrize("name, expected", [
    ("../../evil.pdf", "evil.pdf"),             # 다른 폴더로 저장되지 않게 경로를 뗀다
    ("C:\\Users\\x\\manual.pdf", "manual.pdf"),
])
def test_folder_part_is_removed(name, expected):
    assert safe_filename(name) == expected


@pytest.mark.parametrize("name", ["", ".pdf", "a<b>.pdf", "x" * 200 + ".pdf"])
def test_bad_names_rejected(name):
    with pytest.raises(PdfCheckError):
        safe_filename(name)


# ------------------------------------------------------------------------------
# PDF 추가 → 색인 → documents 기록
# ------------------------------------------------------------------------------
def test_add_pdf_indexes_and_records_all_documents(env):
    db = DocumentsDb()
    indexer = KbIndexer(env, fake_index({"hytrol_138acc": 7, "belt_guide": 3}), run_in_thread=False)
    started, message = indexer.add_pdf(db, make_pdf(pages=4), "belt_guide.pdf")
    assert started and "색인을 시작" in message
    assert (env.admin_add_dir / "belt_guide.pdf").exists()
    rows = db.rows()
    # 올린 PDF 와 초기 PDF 모두 완료로 기록 (초기 문서 행도 여기서 생김). KB 항목은 청크 수에 넣지 않음
    assert rows["belt_guide"]["status"] == "done" and rows["belt_guide"]["pages"] == 4
    assert rows["belt_guide"]["chunks"] == 3 and rows["belt_guide"]["source"] == "admin_add"
    assert rows["hytrol_138acc"]["chunks"] == 7 and rows["hytrol_138acc"]["source"] == "initial"
    assert rows["hytrol_138acc"]["title"] == "2007_583_138acc.pdf"   # sources.csv 의 원본 파일명
    assert rows["belt_guide"]["indexed_at"] is not None


def test_failed_index_removes_new_pdf_and_records_reason(env):
    db = DocumentsDb()
    indexer = KbIndexer(env, failing_index, run_in_thread=False)
    started, _ = indexer.add_pdf(db, make_pdf(), "new_manual.pdf")
    assert started
    row = db.rows()["new_manual"]
    assert row["status"] == "failed" and "vision API timeout" in row["error_message"]
    assert not (env.admin_add_dir / "new_manual.pdf").exists()   # 다음 색인이 같은 이유로 또 실패하지 않게


def test_failed_reindex_restores_previous_file(env):
    old = make_pdf(pages=2)
    (env.admin_add_dir / "guide.pdf").write_bytes(old)
    db = DocumentsDb()
    indexer = KbIndexer(env, failing_index, run_in_thread=False)
    started, message = indexer.add_pdf(db, make_pdf(pages=5), "guide.pdf")
    assert started and "같은 이름" in message
    assert (env.admin_add_dir / "guide.pdf").read_bytes() == old     # 원래 파일로 되돌림
    assert "이전 파일로 되돌림" in db.rows()["guide"]["error_message"]


def test_rejected_file_is_not_saved(env):
    db = DocumentsDb()
    indexer = KbIndexer(env, fake_index({}), run_in_thread=False)
    started, message = indexer.add_pdf(db, make_pdf(text=""), "scan.pdf")
    assert not started and "스캔본" in message
    assert not list(env.admin_add_dir.iterdir()) and db.rows() == {}


def test_same_name_as_initial_rejected(env):
    indexer = KbIndexer(env, fake_index({}), run_in_thread=False)
    started, message = indexer.add_pdf(DocumentsDb(), make_pdf(), "hytrol_138acc_p1-27.pdf")
    assert not started and "초기 매뉴얼과 같은 이름" in message


def test_only_one_index_at_a_time(env):
    indexer = KbIndexer(env, fake_index({}), run_in_thread=False)
    indexer._running = True                     # 다른 PDF 를 색인하는 중
    started, message = indexer.add_pdf(DocumentsDb(), make_pdf(), "second.pdf")
    assert not started and "다른 PDF" in message
    assert not (env.admin_add_dir / "second.pdf").exists()


# ------------------------------------------------------------------------------
# 대시보드 (kb_status · add_pdf)
# ------------------------------------------------------------------------------
def test_kb_status_table(env):
    db = DocumentsDb()
    KbIndexer(env, fake_index({"hytrol_138acc": 7}), run_in_thread=False).add_pdf(db, make_pdf(), "belt.pdf")
    table = AdminDashboard(db).kb_status(db)
    assert list(table.columns) == KB_STATUS_COLUMNS
    assert list(table["파일명"]) == ["hytrol_138acc_p1-27.pdf", "belt.pdf"]   # 초기 문서가 먼저
    assert set(table["상태"]) == {"완료"}
    assert all(len(t) == 11 and t[2] == "/" for t in table["색인 일시"])     # MM/DD HH:MM


def test_empty_kb_status(env):
    table = load_documents(DocumentsDb())
    assert list(table.columns) == KB_STATUS_COLUMNS and table.empty


@pytest.mark.parametrize("user", [None, WORKER])
def test_add_pdf_needs_admin(env, user):
    db = DocumentsDb()
    board = AdminDashboard(db)
    board._kb = KbIndexer(env, fake_index({}), run_in_thread=False)
    started, message = board.add_pdf(make_pdf(), "manual.pdf", user)
    assert not started and "관리자만" in message
    assert db.rows() == {}


def test_add_pdf_by_admin(env):
    db = DocumentsDb()
    board = AdminDashboard(db)
    board._kb = KbIndexer(env, fake_index({"manual": 2}), run_in_thread=False)
    started, _ = board.add_pdf(make_pdf(), "manual.pdf", ADMIN)
    assert started and db.rows()["manual"]["status"] == "done"


def test_test_table_matches_schema_sql():
    # 이 테스트의 documents 열이 chatbot/db/schema.sql 과 같아야 위 테스트가 의미 있다
    columns = [row[1] for row in DocumentsDb().conn.execute("PRAGMA table_info(documents)")]
    assert columns == schema_sql_columns("documents")


# ------------------------------------------------------------------------------
# 목록 채우기 · 색인 중 표시
# ------------------------------------------------------------------------------
def test_fill_missing_documents_from_current_index(env):
    fake_index({"hytrol_138acc": 7})(env)                                # 이미 만들어진 색인 (초기 문서만)
    (env.admin_add_dir / "extra.pdf").write_bytes(make_pdf(pages=2))   # 색인에 없는 PDF
    db = DocumentsDb()
    db.execute("INSERT INTO documents (doc_id, filename, source, status, error_message) VALUES "
               "('old', 'old.pdf', 'admin_add', 'failed', '예전 실패')")
    calls = []
    indexer = KbIndexer(env, lambda settings: calls.append(1), run_in_thread=False)
    assert indexer.fill_missing_documents(db) == 2
    rows = db.rows()
    assert rows["hytrol_138acc"]["status"] == "done" and rows["hytrol_138acc"]["chunks"] == 7
    assert rows["hytrol_138acc"]["indexed_at"] is not None              # 색인 파일을 만든 시각
    assert rows["extra"]["status"] == "failed" and "build_index" in rows["extra"]["error_message"]
    assert rows["old"]["error_message"] == "예전 실패"                   # 이미 있는 행은 그대로
    assert calls == []                                                    # 색인은 다시 만들지 않음
    assert indexer.fill_missing_documents(db) == 0                       # 두 번째부터는 채울 것 없음


def test_counts_come_from_active_index_version(env):
    # build_index.py 는 새 색인을 버전 폴더에 만들고 {chroma_dir}_current.json 에 적는다 → 그 버전에서 세야 함
    fake_index({"hytrol_138acc": 3})(env)                                # 예전 위치(settings.bm25_path)의 오래된 색인
    version = env.chroma_dir.parent / "index_versions" / "v2"
    version.mkdir(parents=True)
    fake_index({"hytrol_138acc": 9})(SimpleNamespace(bm25_path=version / "bm25.pkl"))
    (env.chroma_dir.parent / "index_current.json").write_text(
        '{"chroma_dir": "index_versions/v2", "bm25_path": "index_versions/v2/bm25.pkl"}', encoding="utf-8")
    assert kb_files.active_bm25_path(env) == version / "bm25.pkl"
    db = DocumentsDb()
    KbIndexer(env, fake_index({}), run_in_thread=False).fill_missing_documents(db)
    assert db.rows()["hytrol_138acc"]["chunks"] == 9


def test_fill_skipped_while_indexing(env):
    indexer = KbIndexer(env, fake_index({}), run_in_thread=False)
    indexer._running = True
    db = DocumentsDb()
    assert indexer.fill_missing_documents(db) == 0 and db.rows() == {}


def test_progress_text_only_while_indexing(env):
    seen = []
    indexer = KbIndexer(env, run_in_thread=False,
                        build_index=lambda settings: (seen.append(indexer.progress_text()), fake_index({})(settings)))
    assert indexer.progress_text() == ""
    indexer.add_pdf(DocumentsDb(), make_pdf(), "belt.pdf")
    assert "'belt.pdf' 색인 중" in seen[0] and "시작" in seen[0]          # 색인하는 동안
    assert indexer.progress_text() == ""                                  # 끝난 뒤


def test_indexing_status_label(env):
    db = DocumentsDb()
    db.execute("INSERT INTO documents (doc_id, filename, source, status) VALUES ('a', 'a.pdf', 'admin_add', 'indexing')")
    assert list(load_documents(db)["상태"]) == ["색인 중"]


# ------------------------------------------------------------------------------
# 화면 HTML (Figma 08: 제목줄 · 표 · 상태 배지)
# ------------------------------------------------------------------------------
def test_header_shows_count_and_progress():
    html = render_kb_header(5, "⏳ 'a.pdf' 색인 중… (14:20 시작, 3분째)")
    assert "현재 청킹된 PDF 5건" in html and "kb-progress" in html and "a.pdf" in html
    assert "kb-progress" not in render_kb_header(3)                      # 색인 중이 아니면 안내 없음


def test_table_badges_reason_and_dash():
    db = DocumentsDb()
    db.execute("INSERT INTO documents (doc_id, filename, source, pages, chunks, status, indexed_at) VALUES "
               "('a', 'a.pdf', 'initial', 27, 388, 'done', '2026-10-20 09:00:00')")
    db.execute("INSERT INTO documents (doc_id, filename, source, pages, status) VALUES "
               "('b', 'b.pdf', 'admin_add', 48, 'indexing')")
    db.execute("INSERT INTO documents (doc_id, filename, source, pages, status, error_message) VALUES "
               "('c', 'c.pdf', 'admin_add', 12, 'failed', '색인 실패: 이유')")
    html = render_kb_table(load_documents(db))
    for header in ("파일명", "페이지", "청크 수", "색인 일시", "상태"):
        assert f">{header}</th>" in html
    assert "kb-badge-done'>완료" in html and "kb-badge-running'>색인 중" in html and "kb-badge-failed'>실패" in html
    assert "10/20 09:00" in html and "<td>388</td>" in html
    assert "<td>—</td>" in html                                           # 청크 수가 없는 행
    assert "kb-reason'>색인 실패: 이유" in html                            # 실패 사유는 파일명 아래


def test_table_escapes_file_names():
    db = DocumentsDb()
    db.execute("INSERT INTO documents (doc_id, filename, source, status) VALUES "
               "('x', '<b>x</b>.pdf', 'admin_add', 'done')")
    html = render_kb_table(load_documents(db))
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;.pdf" in html


def test_empty_table_message():
    assert "아직 색인된 PDF 가 없습니다" in render_kb_table(load_documents(DocumentsDb()))
