"""
==============================================================================
 kb_files.py : 화면 08 KB 현황의 실제 처리  (담당: 이정빈 / WBS 3.8.6 · 4.2.3 / 기능 M-06)
==============================================================================

[이 파일이 하는 일]
  관리자가 "PDF 추가하기"로 올린 매뉴얼을 받아 색인까지 처리하고, 결과를 documents 테이블에 남긴다.
    1) check_pdf      : 업로드 파일 검사 (PDF 아님 · 깨짐 · 암호 · 스캔본 · 너무 큼 · 위험한 파일 이름 · 이름 겹침)
    2) save_pdf       : data/raw/admin_add/ 에 저장 (같은 이름이면 새 파일로 바꿈)
    3) KbIndexer      : 색인 전체 재생성(IndexBuilder.main)을 뒤에서 실행. 한 번에 하나만
    4) sync_documents : 색인이 끝나면 PDF 마다 쪽 수 · 청크 수 · 상태를 documents 에 기록
    5) load_documents : documents 를 화면 표(DataFrame)로
  화면(kb_tab.py)과 대시보드(dashboard.py 의 kb_status · add_pdf)는 이 파일의 함수를 부르기만 한다.

[색인 중에는]
  IndexBuilder.main 은 새 색인을 임시 폴더에 다 만든 뒤 바꿔 끼우므로, 끝날 때까지 챗봇은 기존 색인으로 답한다.
  실패하면 기존 색인을 그대로 두고 documents 에 실패 사유를 남긴다.
==============================================================================
"""
from __future__ import annotations

import csv
import logging
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import pandas as pd
import pymupdf

if TYPE_CHECKING:
    from chatbot.core.config import Settings
    from chatbot.db.db import Database

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 50 * 1024 * 1024   # 이보다 큰 PDF 는 받지 않음 (색인 시간·비전 API 비용)
MIN_TEXT_CHARS = 200               # PDF 전체 글자 수가 이보다 적으면 스캔본(글자 없는 PDF)으로 봄
MAX_FILENAME_CHARS = 120           # documents.filename 은 255자지만 Windows 경로 길이를 생각해 짧게
MAX_ERROR_CHARS = 255              # documents.error_message 길이
# 지금 색인에 없는 PDF 를 목록에 채울 때 남기는 사유
NOT_INDEXED_MESSAGE = "아직 색인에 없습니다. 챗봇을 끈 상태에서 python build_index.py 로 색인을 만들어 주세요."

# build_index.py 실행 블록과 같은 값. IndexBuilder.from_settings 가 생기면 그것을 쓰고 이 값은 쓰지 않는다
COMMON_DOC_IDS = {"fenner_belt"}

# documents 의 코드값 → 화면 글자
STATUS_LABELS = {"indexing": "색인 중", "done": "완료", "failed": "실패"}
# KB 현황 표의 열 (Figma 08: 파일명 · 페이지 · 청크 수 · 색인 일시 · 상태). 실패 사유는 실패한 행의 파일명 아래에 보여 줌
KB_STATUS_COLUMNS = ["파일명", "페이지", "청크 수", "색인 일시", "상태", "실패 사유"]

# Windows 파일 이름에 쓸 수 없는 글자
_FORBIDDEN_NAME_CHARS = set('<>:"/\\|?*')


class PdfCheckError(ValueError):
    """업로드 PDF 를 받을 수 없는 이유. 메시지는 그대로 화면에 보여 준다."""


# ------------------------------------------------------------------------------
# 1. 업로드 파일 검사 (WBS 4.2.3 예외 점검)
# ------------------------------------------------------------------------------
def safe_filename(filename: str) -> str:
    """업로드 파일 이름에서 폴더 경로를 떼고, 저장해도 안전한 PDF 파일 이름인지 확인한다.

    받는 것: 사용자가 올린 파일 이름 (예: "../../manual.pdf")
    돌려주는 것: 폴더를 뗀 파일 이름 (예: "manual.pdf")
    "../" 같은 경로로 data/raw/admin_add/ 밖에 저장되는 것을 막는다. 문제가 있으면 PdfCheckError.
    """
    name = Path((filename or "").replace("\\", "/")).name.strip()
    if not name or name.startswith("."):
        raise PdfCheckError("파일 이름이 비어 있습니다. 파일 이름을 확인해 주세요.")
    if not name.lower().endswith(".pdf"):
        raise PdfCheckError("PDF 파일만 추가할 수 있습니다. (.pdf)")
    if len(name) > MAX_FILENAME_CHARS:
        raise PdfCheckError(f"파일 이름이 너무 깁니다. {MAX_FILENAME_CHARS}자 이하로 바꿔 주세요.")
    if _FORBIDDEN_NAME_CHARS & set(name) or any(ord(ch) < 32 for ch in name):
        raise PdfCheckError('파일 이름에 쓸 수 없는 글자가 있습니다. (< > : " / \\ | ? *)')
    return name


def check_pdf(data: bytes, filename: str) -> tuple[str, int]:
    """업로드한 파일이 색인할 수 있는 PDF 인지 검사한다.

    받는 것: 파일 내용(bytes), 파일 이름
    돌려주는 것: (안전한 파일 이름, 쪽 수)
    받을 수 없으면 이유를 담은 PdfCheckError 를 낸다. (PDF 아님 · 깨짐 · 암호 · 스캔본 · 너무 큼)
    """
    name = safe_filename(filename)
    if not data:
        raise PdfCheckError("빈 파일입니다. 파일을 다시 선택해 주세요.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfCheckError(f"파일이 너무 큽니다. {MAX_PDF_BYTES // (1024 * 1024)}MB 이하 PDF 만 추가할 수 있습니다.")
    if b"%PDF-" not in data[:1024]:   # PDF 표준: 머리글은 앞 1024바이트 안에 있다
        raise PdfCheckError("PDF 파일이 아닙니다. 확장자만 .pdf 로 바꾼 파일인지 확인해 주세요.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as error:
        raise PdfCheckError("PDF 파일이 깨져서 열 수 없습니다. 원본 파일을 다시 받아 주세요.") from error
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise PdfCheckError("암호가 걸린 PDF 는 추가할 수 없습니다. 암호를 푼 파일을 올려 주세요.")
        if doc.page_count == 0:
            raise PdfCheckError("쪽이 없는 PDF 입니다. 원본 파일을 다시 받아 주세요.")
        try:
            text_chars = sum(len(page.get_text().strip()) for page in doc)
        except Exception as error:
            raise PdfCheckError("PDF 파일이 깨져서 글자를 읽을 수 없습니다. 원본 파일을 다시 받아 주세요.") from error
        if text_chars < MIN_TEXT_CHARS:
            raise PdfCheckError("글자를 읽을 수 없는 PDF(스캔본)입니다. 글자가 들어 있는 PDF 를 올려 주세요.")
        return name, doc.page_count
    finally:
        doc.close()


def save_pdf(data: bytes, name: str, folder: Path) -> Path:
    """PDF 를 folder 에 저장한다. 같은 이름이 있으면 새 파일로 바꾼다.

    받는 것: 파일 내용, safe_filename 을 거친 파일 이름, 저장 폴더(data/raw/admin_add)
    돌려주는 것: 저장한 파일 경로
    임시 파일에 다 쓴 뒤 이름을 바꿔, 저장 중에 멈춰도 반쯤 쓴 PDF 가 색인되지 않게 한다.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    temporary = folder / f".{name}.uploading"
    temporary.write_bytes(data)
    temporary.replace(path)
    return path


# ------------------------------------------------------------------------------
# 2. 문서 정보 (doc_id · 문서명 · 구분)
# ------------------------------------------------------------------------------
def read_sources(sources_path: Path) -> dict[str, dict]:
    """sources.csv 를 {파일 이름: 행} 으로 읽는다. (없으면 빈 dict)"""
    if not sources_path.exists():
        return {}
    with sources_path.open(encoding="utf-8-sig", newline="") as file:
        return {row["file"]: row for row in csv.DictReader(file)}


def document_info(pdf_path: Path, raw_dir: Path, sources: dict[str, dict]) -> dict:
    """PDF 한 건의 documents 행 정보(doc_id · 파일명 · 문서명 · 구분)를 만든다.

    받는 것: PDF 경로, 원본 폴더(data/raw), sources.csv 내용
    돌려주는 것: {"doc_id", "filename", "title", "source"}
    doc_id 는 문서 추출(PdfExtractor.doc_id_of)과 같은 규칙: sources.csv 에 있으면 그 doc_id, 없으면 파일 이름
    """
    row = sources.get(pdf_path.name, {})
    is_initial = pdf_path.resolve().is_relative_to((raw_dir / "initial").resolve())
    return {
        "doc_id": row.get("doc_id") or pdf_path.stem,
        "filename": pdf_path.name,
        "title": row.get("original_file") or pdf_path.stem,
        "source": "initial" if is_initial else "admin_add",
    }


def list_pdfs(raw_dir: Path) -> list[Path]:
    """data/raw/initial · data/raw/admin_add 의 PDF 목록 (색인 대상과 같은 범위)."""
    folders = [raw_dir / "initial", raw_dir / "admin_add"]
    return sorted(p for folder in folders if folder.exists() for p in folder.glob("*") if p.suffix.lower() == ".pdf")


def check_name_conflict(name: str, raw_dir: Path, sources: dict[str, dict]) -> None:
    """올리려는 PDF 가 다른 문서와 같은 doc_id 가 되지 않는지 확인한다. 겹치면 PdfCheckError.

    같은 이름의 관리자 추가 PDF 는 "다시 색인"으로 보고 허용한다. (명세서: 같은 파일명이면 다시 색인)
    초기 문서와 같은 이름이거나 doc_id 가 다른 PDF 와 겹치면 색인이 실패하므로 미리 막는다.
    """
    admin_path = raw_dir / "admin_add" / name
    if (raw_dir / "initial" / name).exists():
        raise PdfCheckError("초기 매뉴얼과 같은 이름입니다. 파일 이름을 바꿔 주세요.")
    new_id = document_info(admin_path, raw_dir, sources)["doc_id"]
    for path in list_pdfs(raw_dir):
        if path.name != name and document_info(path, raw_dir, sources)["doc_id"] == new_id:
            raise PdfCheckError(f"'{path.name}' 과 같은 문서로 인식됩니다. 파일 이름을 바꿔 주세요.")


# ------------------------------------------------------------------------------
# 3. documents 테이블 읽기·쓰기
# ------------------------------------------------------------------------------
def upsert_document(db: Database, row: dict) -> None:
    """documents 에 한 행을 넣거나(없으면) 고친다(있으면).

    받는 것: Database, {"doc_id", "filename", "title", "source", "pages", "chunks", "status", "error_message", "indexed_at"}
    돌려주는 것: 없음
    """
    exists = db.fetch("SELECT doc_id FROM documents WHERE doc_id = %s", (row["doc_id"],))
    values = (row["filename"], row["title"], row["source"], row.get("pages"), row.get("chunks"),
              row["status"], row.get("error_message"), row.get("indexed_at"))
    if exists:
        db.execute("UPDATE documents SET filename = %s, title = %s, source = %s, pages = %s, chunks = %s, "
                   "status = %s, error_message = %s, indexed_at = %s WHERE doc_id = %s", (*values, row["doc_id"]))
    else:
        db.execute("INSERT INTO documents (filename, title, source, pages, chunks, status, error_message, indexed_at, "
                   "doc_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (*values, row["doc_id"]))


def load_documents(db: Database) -> pd.DataFrame:
    """documents 를 KB 현황 표로 읽는다.

    받는 것: Database
    돌려주는 것: DataFrame (열: KB_STATUS_COLUMNS). 초기 문서 → 관리자 추가 순, 같은 구분 안에서는 파일명 순
    """
    rows = db.fetch("SELECT filename, source, pages, chunks, indexed_at, status, error_message FROM documents", ())
    # ENUM 정렬 규칙은 DB 마다 달라서 순서는 여기서 정한다
    rows = sorted(rows, key=lambda row: (row["source"] != "initial", row["filename"]))
    table = [[
        row["filename"],
        row["pages"],
        row["chunks"],
        _format_time(row["indexed_at"]),
        STATUS_LABELS.get(row["status"], row["status"]),
        row["error_message"] or "",
    ] for row in rows]
    return pd.DataFrame(table, columns=KB_STATUS_COLUMNS)


def active_bm25_path(settings: Settings) -> Path:
    """지금 검색에 쓰는 색인의 BM25 파일. (청크 수 · 색인 시각을 여기서 읽는다)

    받는 것: Settings (chroma_dir · bm25_path)
    돌려주는 것: Path. build_index.py 가 만든 버전 색인({chroma_dir}_current.json)이 있으면 그 파일,
      없으면 settings.bm25_path (검색기 Retriever 와 같은 규칙: retriever.active_index_paths)
    """
    from chatbot.rag.retriever import active_index_paths   # 검색 모듈(chromadb 등)은 필요할 때만 불러온다

    return active_index_paths(settings.chroma_dir, settings.bm25_path)[1]


def count_chunks(bm25_path: Path) -> Counter:
    """새 색인(BM25 항목 목록)에서 문서별 청크 수를 센다. KB 항목은 청크가 아니라 세지 않는다.

    받는 것: BM25 파일 (active_bm25_path)
    돌려주는 것: Counter {doc_id: 청크 수}. 색인 파일이 없으면 빈 Counter
    """
    from chatbot.rag.retriever import load_bm25_items   # 검색 모듈(chromadb 등)은 필요할 때만 불러온다

    if not bm25_path.exists():
        return Counter()
    return Counter(item["metadata"]["doc_id"] for item in load_bm25_items(bm25_path)
                   if item["metadata"].get("chunk_type") != "kb")


def _format_time(value) -> str:
    """색인 시각을 표에 보일 글자로 (예: 10/20 09:00, 없으면 빈칸)."""
    if isinstance(value, str) and value:
        try:   # sqlite 등은 시각을 글자로 돌려줌
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        return f"{value:%m/%d %H:%M}"
    return str(value or "")


# ------------------------------------------------------------------------------
# 4. 색인 실행 (한 번에 하나, 뒤에서)
# ------------------------------------------------------------------------------
def run_index_builder(settings: Settings) -> None:
    """IndexBuilder.main 으로 색인을 처음부터 다시 만든다. (새 PDF 만 추출하고 기존 문서는 캐시 재사용)

    받는 것: Settings
    돌려주는 것: 없음 (실패하면 예외. 기존 색인은 그대로 남는다)
    """
    from build_index import IndexBuilder   # 색인 모듈은 무거워서 PDF 를 추가할 때만 불러온다

    if hasattr(IndexBuilder, "from_settings"):
        builder = IndexBuilder.from_settings(settings)
    else:
        builder = IndexBuilder(
            embedding_model=settings.embedding_model,
            bm25_path=settings.bm25_path,
            chroma_dir=settings.chroma_dir,
            common_doc_ids=COMMON_DOC_IDS,
            settings=settings,
        )
    builder.main()


class KbIndexer:
    """PDF 추가 → 색인 재생성 → documents 기록을 한 번에 하나씩 처리한다.

    앱 전체에 하나만 둔다(AdminDashboard 가 가짐). 색인은 서버 자원이라 사용자별 값이 아니다.
    """

    def __init__(self, settings: Settings | None = None,
                 build_index: Callable[[Settings], None] = run_index_builder,
                 run_in_thread: bool = True):
        """
        받는 것:
          settings      : Settings. 없으면 처음 쓸 때 Settings.load() 로 읽는다
          build_index   : 색인을 다시 만드는 함수 (테스트에서는 가짜로 바꿈)
          run_in_thread : True 면 색인을 뒤에서 실행하고 바로 돌아감 (테스트에서는 False)
        """
        self._settings = settings
        self._build_index = build_index
        self._run_in_thread = run_in_thread
        self._lock = threading.Lock()   # _running 을 여러 요청이 동시에 바꾸지 못하게
        self._running = False
        self._job_name = ""             # 지금 색인 중인 PDF 이름 (진행 안내용)
        self._started_at: datetime | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            from chatbot.core.config import Settings

            self._settings = Settings.load()
        return self._settings

    def is_running(self) -> bool:
        """색인이 지금 돌고 있는지."""
        return self._running

    def progress_text(self) -> str:
        """색인 중이면 화면에 보일 진행 안내(어떤 PDF · 언제 시작 · 몇 분째), 아니면 빈 글자.

        받는 것: 없음
        돌려주는 것: Markdown 글자
        """
        started_at = self._started_at
        if not self._running or started_at is None:
            return ""
        minutes = int((datetime.now() - started_at).total_seconds() // 60)
        return (f"⏳ **'{self._job_name}' 색인 중…** ({started_at:%H:%M} 시작, {minutes}분째) "
                "끝날 때까지 다른 PDF 는 추가할 수 없습니다. 그동안 챗봇은 기존 매뉴얼로 답합니다.")

    def add_pdf(self, db: Database, data: bytes, filename: str) -> tuple[bool, str]:
        """업로드 PDF 를 검사·저장하고 색인을 시작한다.

        받는 것: Database, 파일 내용, 파일 이름
        돌려주는 것: (시작했는지, 화면 안내 문구)
        검사에서 걸리면 저장도 색인도 하지 않는다 (기존 색인 그대로).
        """
        settings = self.settings
        sources = read_sources(Path(settings.raw_dir).parent / "sources.csv")
        try:
            name, pages = check_pdf(data, filename)
            check_name_conflict(name, Path(settings.raw_dir), sources)
        except PdfCheckError as error:
            return False, str(error)

        with self._lock:
            if self._running:
                return False, "다른 PDF 를 색인하고 있습니다. 끝난 뒤 다시 추가해 주세요."
            self._running = True
            self._job_name = name
            self._started_at = datetime.now()

        try:
            path = Path(settings.admin_add_dir) / name
            backup = self._backup_path(name)
            replaced = path.exists()
            if replaced:   # 같은 이름 PDF 를 바꿀 때: 색인이 실패하면 되돌릴 수 있게 원래 파일을 옮겨 둔다
                backup.parent.mkdir(parents=True, exist_ok=True)
                path.replace(backup)
            save_pdf(data, name, Path(settings.admin_add_dir))
            info = document_info(path, Path(settings.raw_dir), sources)
            upsert_document(db, {**info, "pages": pages, "chunks": None, "status": "indexing",
                                 "error_message": None, "indexed_at": None})
        except Exception:
            self._running = False
            logger.exception("PDF 저장 실패")
            return False, "PDF 를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."

        job = {"doc_id": info["doc_id"], "path": path, "backup": backup if replaced else None}
        if self._run_in_thread:
            threading.Thread(target=self._rebuild, args=(db, job), daemon=True).start()
        else:
            self._rebuild(db, job)
        prefix = "같은 이름의 PDF 를 새 파일로 바꿨습니다. " if replaced else ""
        return True, f"{prefix}'{name}' 색인을 시작했습니다. 몇 분 걸릴 수 있습니다. 목록에서 상태를 확인해 주세요."

    def _backup_path(self, name: str) -> Path:
        """같은 이름 PDF 를 바꿀 때 원래 파일을 잠시 옮겨 둘 곳. data/raw 밖이어야 색인 대상에 들어가지 않는다."""
        return Path(self.settings.interim_dir) / "kb_upload_backup" / name

    def _rebuild(self, db: Database, job: dict) -> None:
        """색인을 다시 만들고 결과를 documents 에 기록한다. (예외를 밖으로 내지 않음)

        받는 것: Database, {"doc_id", "path": 올린 PDF, "backup": 바꾸기 전 원래 PDF(없으면 None)}
        실패하면 올린 PDF 를 치우고(원래 파일이 있었으면 되돌리고) 실패 사유를 남긴다.
        그래야 폴더가 기존 색인과 같아지고, 다음 PDF 추가가 같은 이유로 또 실패하지 않는다.
        """
        try:
            self._build_index(self.settings)
            self.sync_documents(db)
            if job["backup"] is not None:
                job["backup"].unlink(missing_ok=True)
        except Exception as error:
            logger.exception("색인 재생성 실패")
            note = ""
            try:
                if job["backup"] is not None:
                    job["backup"].replace(job["path"])
                    note = " (이전 파일로 되돌림)"
                else:
                    job["path"].unlink(missing_ok=True)
            except Exception:
                logger.exception("올린 PDF 정리 실패")
            reason = f"색인 실패{note}: {type(error).__name__}: {error}"[:MAX_ERROR_CHARS]
            try:
                db.execute("UPDATE documents SET status = %s, error_message = %s WHERE doc_id = %s",
                           ("failed", reason, job["doc_id"]))
            except Exception:
                logger.exception("documents 실패 기록 저장 실패")
        finally:
            self._running = False
            self._started_at = None

    def sync_documents(self, db: Database) -> int:
        """지금 색인 기준으로 모든 PDF 의 쪽 수 · 청크 수를 documents 에 기록한다. (상태 = 완료)

        받는 것: Database
        돌려주는 것: 기록한 문서 수
        색인 재생성이 끝난 뒤 부른다. 초기 문서 행도 여기서 생긴다.
        """
        settings = self.settings
        raw_dir = Path(settings.raw_dir)
        sources = read_sources(raw_dir.parent / "sources.csv")
        chunks = count_chunks(active_bm25_path(settings))
        now = datetime.now().replace(microsecond=0)
        pdfs = list_pdfs(raw_dir)
        for path in pdfs:
            info = document_info(path, raw_dir, sources)
            upsert_document(db, {**info, "pages": _page_count(path), "chunks": chunks.get(info["doc_id"], 0),
                                 "status": "done", "error_message": None, "indexed_at": now})
        return len(pdfs)

    def fill_missing_documents(self, db: Database) -> int:
        """documents 에 아직 없는 PDF 행을 지금 색인 기준으로 채운다. 색인은 다시 만들지 않는다 (API 사용 없음).

        받는 것: Database
        돌려주는 것: 새로 채운 문서 수
        초기 문서 행은 색인 재생성이 성공해야 생기므로, 그 전(또는 실패 뒤)에도 목록에 보이게 KB 현황 탭을 열 때 부른다.
          - 지금 색인에 청크가 있는 PDF → 완료 (색인 시각 = 색인 파일을 만든 시각)
          - 색인에 없는 PDF → 실패 + NOT_INDEXED_MESSAGE
        이미 있는 행은 건드리지 않고, 색인 중에는 아무것도 하지 않는다.
        """
        if self._running:
            return 0
        settings = self.settings
        raw_dir = Path(settings.raw_dir)
        sources = read_sources(raw_dir.parent / "sources.csv")
        existing = {row["doc_id"] for row in db.fetch("SELECT doc_id FROM documents", ())}
        missing = [(path, info) for path in list_pdfs(raw_dir)
                   if (info := document_info(path, raw_dir, sources))["doc_id"] not in existing]
        if not missing:
            return 0
        bm25_path = active_bm25_path(settings)
        chunks = count_chunks(bm25_path)
        built_at = datetime.fromtimestamp(bm25_path.stat().st_mtime).replace(microsecond=0) if bm25_path.exists() else None
        for path, info in missing:
            count = chunks.get(info["doc_id"], 0)
            upsert_document(db, {**info, "pages": _page_count(path), "chunks": count or None,
                                 "status": "done" if count else "failed",
                                 "error_message": None if count else NOT_INDEXED_MESSAGE,
                                 "indexed_at": built_at if count else None})
        return len(missing)


def _page_count(path: Path) -> int:
    """PDF 쪽 수."""
    with pymupdf.open(path) as doc:
        return doc.page_count
