"""
==============================================================================
 scripts/seed.py : 테이블 만들기 + 초기 계정 넣기  (담당: 이정빈 / WBS 3.2.1 · 5.1.1 / 기능 A-01)
==============================================================================
 
[이 파일이 하는 일]
  1) .env 의 DB 접속 정보로 MySQL(Aiven)에 접속한다.            (config.Settings.load)
  2) .env 에서 초기 계정 비밀번호를 읽는다.                       (config.load_seed_passwords)
  3) chatbot/db/schema.sql 을 실행해 테이블을 만든다.             (이미 있으면 건너뜀)
     예전 정의로 만든 users 테이블이면 지금 정의로 맞춘다.       (role 'engineer' → 'user', 해시 칸 길이)
  4) 아래 DEFAULT_ACCOUNTS 의 계정을 users 테이블에 넣는다.        (비밀번호는 해시로 바꿔서)
  5) 최근 7일치 데모 로그를 chat_logs · kb_gaps 에 넣는다.            (대시보드 시연용, --no-demo 로 끔)
 
[비밀번호는 어디에?]
  이 파일에는 사원번호·이름·권한과 "비밀번호를 읽을 .env 항목 이름"만 적는다.
  비밀번호 원문은 .env(Git 제외)에만 있다. → GitHub·제출 zip 에 비밀번호가 남지 않는다.
 
[여러 번 실행해도 안전한가]  예. (WBS 5.1.1)
  - 테이블은 CREATE TABLE IF NOT EXISTS 라서 기존 데이터를 지우지 않는다.
  - 같은 사원번호가 있으면 새로 넣지 않고 이름·권한·비밀번호를 고친다. (중복 계정 없음)
  - 바뀐 게 없는 계정은 건드리지 않는다.
  - 아래 목록에 없는 기존 계정은 지우지 않는다.
  - 데모 로그는 데모 계정(D0001)의 로그만 지우고 다시 넣는다. (실제 사원 로그는 그대로)
 
[실행 전 준비]
  1) .env 가 모두 채워져 있어야 한다. (python chatbot/core/config.py 가 [OK])
  2) .env 에 아래 두 줄 추가 (각자 8자 이상으로 정하기)
       SEED_ADMIN_PASSWORD=...
       SEED_USER_PASSWORD=...
  3) pip install -r requirements.txt   (pymysql 포함)
 
[실행]  프로젝트 최상단 폴더에서, 가상환경을 켠 상태로
  python scripts/seed.py
  python scripts/seed.py --no-demo   # 데모 로그 없이 테이블·계정만
==============================================================================
"""
import hashlib   # 비밀번호 해시 계산 (PBKDF2)
import hmac      # 두 해시를 안전하게 비교 (compare_digest)
import argparse  # 실행 옵션(--no-demo) 읽기
import json      # 데모 로그의 JSON 칸(kb_ids · latency_ms)
import random    # 데모 로그 시각·점수 만들기 (같은 날이면 같은 값)
import secrets   # 예측할 수 없는 무작위 값 생성 (소금값)
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
 
# 이 파일은 scripts/ 안에 있어서, 그대로 두면 chatbot 패키지(chatbot/core/config.py)를 못 찾는다.
# → 최상단 폴더(scripts 의 부모)를 파이썬 검색 경로 맨 앞에 넣는다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
 
import pymysql                              # noqa: E402  MySQL 접속 라이브러리
from pymysql.constants import CLIENT        # noqa: E402  접속 옵션 상수
 
# ※ "from config import ..." 로 쓰면 가상환경에 설치된 다른 config 패키지를 가져온다. 전체 경로로 쓴다.
from chatbot.core.config import BASE_DIR, ConfigError, Settings, load_seed_passwords   # noqa: E402
from chatbot.db.db import TIME_ZONE                     # noqa: E402  시간대 기준 (db.py 와 같게)
from chatbot.core.logger import LOG_COLUMNS                # noqa: E402  chat_logs 열 (logger.py 와 같게)
 
 
# ------------------------------------------------------------------------------
# 0. 비밀번호 해시 (예전 passwords.py 의 내용을 그대로 옮김)
# ------------------------------------------------------------------------------
# 저장 형식: pbkdf2_sha256$반복횟수$소금값(16진수)$해시값(16진수)
# ※ 로그인 확인(auth.py, 김현준)도 반드시 이 형식·방식과 똑같이 검사해야 로그인이 된다.
HASH_ALGORITHM = "pbkdf2_sha256"
HASH_ITERATIONS = 600_000   # 반복 횟수 (많을수록 무작위 대입 공격이 느려진다)
SALT_BYTES = 16             # 소금값 길이 (16바이트 = 16진수 32글자)
 
 
def hash_password(password: str) -> str:
    """비밀번호 원문을 저장용 해시 문자열로 바꾼다.
 
    받는 것: 비밀번호 원문
    돌려주는 것: "pbkdf2_sha256$반복횟수$소금값$해시값" 형태의 문자열
    같은 비밀번호를 넣어도 소금값이 매번 달라서 결과는 매번 다르다. (정상)
    """
    if not password:
        raise ValueError("비밀번호가 비어 있습니다")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS)
    return f"{HASH_ALGORITHM}${HASH_ITERATIONS}${salt.hex()}${digest.hex()}"
 
 
def verify_password(password: str, stored_hash: str) -> bool:
    """입력한 비밀번호가 저장된 해시와 맞는지 확인한다.
 
    받는 것: 비밀번호 원문, DB에 저장된 해시 문자열
    돌려주는 것: 맞으면 True, 틀리거나 해시 형식이 이상하면 False
    (seed.py 에서는 "바뀐 게 없는 계정은 건드리지 않기" 판단에 쓴다)
    """
    try:
        algorithm, iterations_text, salt_hex, digest_hex = stored_hash.split("$")
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    if algorithm != HASH_ALGORITHM:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    # == 대신 compare_digest: 걸린 시간으로 정답을 추측하는 공격을 막기 위해 항상 같은 시간으로 비교
    return hmac.compare_digest(digest, expected)
 
 
# ------------------------------------------------------------------------------
# 1. 초기 계정 목록  ← 계정을 추가·수정할 때는 여기만 고친다
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class AccountSpec:
    """넣을 계정 하나의 정보. 비밀번호 대신 "비밀번호를 읽을 .env 항목 이름"을 적는다."""
 
    employee_id: str    # 사원번호 (로그인 아이디, 20자 이하)
    name: str           # 화면에 보일 이름 (50자 이하)
    role: str           # "user"(일반 사원) 또는 "admin"(관리자)
    password_env: str   # 비밀번호를 읽을 .env 항목 이름
 
 
# 같은 .env 항목을 여러 계정이 함께 써도 된다. (예: 사원 계정은 모두 SEED_USER_PASSWORD)
DEFAULT_ACCOUNTS: list[AccountSpec] = [
    AccountSpec("A0001", "관리자", "admin", "SEED_ADMIN_PASSWORD"),
    AccountSpec("E0001", "이정빈", "user", "SEED_USER_PASSWORD"),
    AccountSpec("D0001", "데모 사원", "user", "SEED_USER_PASSWORD"),   # 데모 로그(4-1) 전용 계정
]
 
 
# ------------------------------------------------------------------------------
# 2. 고정값
# ------------------------------------------------------------------------------
SCHEMA_PATH = BASE_DIR / "chatbot" / "db" / "schema.sql"   # 테이블 만드는 SQL
ALLOWED_ROLES = ("user", "admin")              # users.role 에 들어갈 수 있는 값 (schema.sql ENUM 과 같게)
OLD_ROLE = "engineer"                          # 예전 schema.sql 의 일반 사원 값 → "user" 로 바꾼다
PASSWORD_HASH_LEN = 255                        # users.password_hash VARCHAR(255) (pbkdf2 해시는 118자)
EMPLOYEE_ID_MAX = 20                           # users.employee_id VARCHAR(20)
NAME_MAX = 50                                  # users.name VARCHAR(50)
 
# 같은 사원번호가 있으면 오류 대신 기존 행을 고친다. (ON DUPLICATE KEY UPDATE)
# %s 자리에는 값이 "따로" 전달되어 라이브러리가 안전하게 넣어 준다. (SQL 인젝션 방지)
# ※ VALUES(칸) 문법은 MySQL 8 에서 "앞으로 바뀔 예정" 경고가 있지만 정상 동작하고,
#   로컬 MySQL·MariaDB 백업 환경에서도 똑같이 돌아가서 이 문법을 쓴다.
UPSERT_SQL = """
INSERT INTO users (employee_id, password_hash, name, role)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    password_hash = VALUES(password_hash),
    name = VALUES(name),
    role = VALUES(role)
"""
 
 
class SeedError(Exception):
    """계정 목록·스키마 파일 문제를 알릴 때 쓰는 오류."""
 
 
@dataclass(frozen=True)
class SeedAccount:
    """실제로 DB 에 넣을 계정. 비밀번호는 repr=False 라서 print 해도 나오지 않는다."""
 
    employee_id: str
    name: str
    role: str
    password: str = field(repr=False)
 
 
# ------------------------------------------------------------------------------
# 3. 계정 목록 검사·완성
# ------------------------------------------------------------------------------
def check_account_specs(specs: list[AccountSpec]) -> None:
    """DEFAULT_ACCOUNTS 에 적힌 내용이 users 테이블 규칙에 맞는지 검사한다.
 
    받는 것: 계정 목록
    돌려주는 것: 없음. 문제가 있으면 모아서 SeedError 를 낸다.
    (DB 에 넣다가 중간에 실패하는 것보다, 넣기 전에 잡는 것이 안전하다)
    """
    problems: list[str] = []
    seen_ids: set[str] = set()
    for spec in specs:
        label = f"계정 {spec.employee_id or '(사원번호 없음)'}"
        if not spec.employee_id:
            problems.append(f"{label}: 사원번호가 비어 있습니다")
        elif len(spec.employee_id) > EMPLOYEE_ID_MAX:
            problems.append(f"{label}: 사원번호는 {EMPLOYEE_ID_MAX}자 이하여야 합니다")
        elif spec.employee_id in seen_ids:
            problems.append(f"{label}: 사원번호가 중복됩니다")
        seen_ids.add(spec.employee_id)
        if not spec.name or len(spec.name) > NAME_MAX:
            problems.append(f"{label}: 이름은 1~{NAME_MAX}자여야 합니다")
        if spec.role not in ALLOWED_ROLES:
            problems.append(f"{label}: role 은 user 또는 admin 이어야 합니다 (현재: {spec.role})")
        if not spec.password_env:
            problems.append(f"{label}: 비밀번호를 읽을 .env 항목 이름이 비어 있습니다")
 
    if not specs:
        problems.append("등록할 계정이 하나도 없습니다")
    elif not any(spec.role == "admin" for spec in specs):
        problems.append("관리자(admin) 계정이 하나도 없습니다 → 관리자 대시보드에 들어갈 수 없게 됩니다")
 
    if problems:
        numbered = "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, start=1))
        raise SeedError(f"DEFAULT_ACCOUNTS 오류 {len(problems)}건 (scripts/seed.py 상단 확인):\n{numbered}")
 
 
def build_accounts(specs: list[AccountSpec], passwords: dict[str, str]) -> list[SeedAccount]:
    """계정 정보에 .env 에서 읽은 비밀번호를 붙여 실제로 넣을 계정 목록을 만든다.
 
    받는 것: 계정 목록, {.env 항목 이름: 비밀번호} 사전
    돌려주는 것: list[SeedAccount]
    """
    return [
        SeedAccount(spec.employee_id, spec.name, spec.role, passwords[spec.password_env])
        for spec in specs
    ]
 
 
# ------------------------------------------------------------------------------
# 4. DB 작업
# ------------------------------------------------------------------------------
def connect(settings: Settings) -> pymysql.connections.Connection:
    """config 의 접속 정보로 MySQL 에 접속한다.
 
    받는 것: Settings
    돌려주는 것: DB 연결 객체
    ※ 공통 db.py(Database.connect)가 완성되면 그것으로 바꿀 수 있다.
    """
    # 인증서 경로가 있으면 SSL 로 접속(Aiven), 없으면 SSL 없이 접속(로컬 MySQL 백업용)
    ssl_options = {"ca": str(settings.db_ssl_ca)} if settings.db_ssl_ca else None
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        charset="utf8mb4",            # 한글이 깨지지 않게
        ssl=ssl_options,
        connect_timeout=10,           # 10초 안에 접속 못 하면 포기 (Aiven 서비스 꺼짐 등)
        autocommit=False,             # 마지막에 commit 할 때만 저장 → 중간에 실패하면 전부 취소
        # schema.sql 처럼 SQL 여러 개가 들어 있는 파일을 한 번에 실행하게 허용한다.
        # (주석·문자열 속 ; 를 파이썬이 직접 자르지 않고 MySQL 이 정확히 해석한다)
        client_flag=CLIENT.MULTI_STATEMENTS,
        # Aiven 서버는 UTC 라 그대로 두면 계정 생성 시각(created_at)만 9시간 어긋난다. 앱(db.py)과 같은 한국 시간으로 맞춘다.
        init_command=f"SET time_zone = '{TIME_ZONE}'",
    )
 
 
def apply_schema(conn: pymysql.connections.Connection, path: Path) -> None:
    """schema.sql 을 실행해 테이블을 만든다. (이미 있는 테이블은 건너뜀)
 
    받는 것: DB 연결, schema.sql 경로
    돌려주는 것: 없음
    """
    if not path.exists():
        raise SeedError(f"스키마 파일이 없습니다: {path}")
    sql = path.read_text(encoding="utf-8-sig")
    with conn.cursor() as cursor:
        cursor.execute(sql)
        # SQL 여러 개를 실행하면 결과도 여러 개가 돌아온다. 끝까지 다 받아야 다음 작업을 할 수 있다.
        while cursor.nextset():
            pass
 
 
def upgrade_users_table(conn: pymysql.connections.Connection) -> list[str]:
    """예전 schema.sql 로 이미 만든 users 테이블을 지금 정의에 맞춘다. (이미 맞으면 아무것도 안 함)
 
    받는 것: DB 연결
    돌려주는 것: 바꾼 내용 목록 (바꾼 게 없으면 빈 목록)
    왜 필요한가:
      schema.sql 은 CREATE TABLE IF NOT EXISTS 라서 테이블이 이미 있으면 바뀐 정의가 반영되지 않는다.
      예전 정의 그대로면 계정을 넣을 때 오류가 난다.
        - role ENUM('engineer','admin')  → role='user' 를 넣으면 "Data truncated"
        - password_hash VARCHAR(100)      → pbkdf2 해시(118자)를 넣으면 "Data too long"
    """
    changes: list[str] = []
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
            "AND COLUMN_NAME IN ('role', 'password_hash')"
        )
        columns = {row[0].lower(): row for row in cursor.fetchall()}
 
        # 1) role: 'engineer' 가 남아 있으면 → 'user' 추가 → 기존 행 변경 → 'engineer' 제거 (기존 계정 유지)
        role = columns.get("role")
        if role is not None and f"'{OLD_ROLE}'" in role[1]:
            cursor.execute("ALTER TABLE users MODIFY role ENUM('engineer', 'user', 'admin') NOT NULL DEFAULT 'user'")
            cursor.execute("UPDATE users SET role = 'user' WHERE role = %s", (OLD_ROLE,))
            cursor.execute("ALTER TABLE users MODIFY role ENUM('user', 'admin') NOT NULL DEFAULT 'user'")
            changes.append(f"users.role: '{OLD_ROLE}' → 'user'")
 
        # 2) password_hash: 칸이 짧으면 늘린다 (줄이지는 않음)
        password_hash = columns.get("password_hash")
        if password_hash is not None and (password_hash[2] or 0) < PASSWORD_HASH_LEN:
            cursor.execute(f"ALTER TABLE users MODIFY password_hash VARCHAR({PASSWORD_HASH_LEN}) NOT NULL")
            changes.append(f"users.password_hash: VARCHAR({password_hash[2]}) → VARCHAR({PASSWORD_HASH_LEN})")
    return changes
 
 
def upsert_accounts(conn: pymysql.connections.Connection, accounts: list[SeedAccount]) -> dict[str, int]:
    """계정을 users 테이블에 넣거나 고친다.
 
    받는 것: DB 연결, 넣을 계정 목록
    돌려주는 것: {"added": 새로 넣은 수, "updated": 고친 수, "unchanged": 그대로 둔 수}
    """
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    ids = [a.employee_id for a in accounts]
    placeholders = ", ".join(["%s"] * len(ids))   # 예: 계정 2개면 "%s, %s"
 
    with conn.cursor() as cursor:
        # 1) 지금 DB에 있는 같은 사원번호 계정을 한 번에 읽어 온다.
        #    (f-string 으로 만드는 건 %s 자리 개수뿐이고, 실제 값은 ids 로 따로 전달 → 안전)
        cursor.execute(
            f"SELECT employee_id, password_hash, name, role FROM users WHERE employee_id IN ({placeholders})",
            ids,
        )
        existing = {row[0]: row for row in cursor.fetchall()}
 
        for account in accounts:
            old = existing.get(account.employee_id)
 
            # 2) 이름·권한·비밀번호가 모두 같으면 건드리지 않는다.
            #    (매번 새로 해시하면 소금값 때문에 해시가 바뀌어, 바뀐 게 없는데도 "수정"으로 기록되기 때문)
            if (old is not None and old[2] == account.name and old[3] == account.role
                    and verify_password(account.password, old[1])):
                counts["unchanged"] += 1
                continue
 
            # 3) 없으면 새로 넣고, 있으면 고친다. 비밀번호는 여기서 해시로 바뀐다.
            cursor.execute(
                UPSERT_SQL,
                (account.employee_id, hash_password(account.password), account.name, account.role),
            )
            counts["updated" if old is not None else "added"] += 1
 
    conn.commit()   # 여기까지 오류 없이 왔을 때만 한꺼번에 저장한다
    return counts
 
 
# ------------------------------------------------------------------------------
# 4-1. 데모 로그 (WBS 5.1.1)  ← 대시보드 운영 현황·답변 품질 탭 시연용
# ------------------------------------------------------------------------------
# 최근 7일치 질문 기록을 chat_logs(+ 검토 상태 kb_gaps)에 넣어 대시보드가 비어 있지 않게 한다.
# 데모 로그는 모두 데모 계정(D0001)의 것이다. 다시 실행하면 D0001 로그만 지우고 새로 넣는다.
#   → 여러 번 돌려도 쌓이지 않고, 실제 사원의 로그는 건드리지 않는다.
DEMO_EMPLOYEE_ID = "D0001"
DEMO_DAYS = 7               # 오늘을 포함한 최근 며칠에 나눠 넣을지 (대시보드 기본 기간 "최근 7일")
DEMO_RANDOM_SEED = 1102     # 같은 날 다시 돌리면 같은 데모 로그가 나오게 고정 (발표일 11/02)
DEMO_REVIEWER_ID = "A0001"  # 검토 상태(kb_gaps)의 처리자. DEFAULT_ACCOUNTS 의 관리자
DEMO_ERROR = "generate/llm_timeout: 답변 생성이 30초 안에 끝나지 않았습니다"


@dataclass(frozen=True)
class DemoScenario:
    """데모 질문 한 종류. repeat 번 기록된다.

    outcome (응답 결과):
      answered 답변 / not_found 답 못 찾음 / nameplate 명판 인식만 / retake 재촬영 안내 /
      out_of_scope 정비와 무관한 질문 / error 오류
    input_type · question_type 은 chat_logs 에 저장되는 값 그대로 (logger.py · schema.sql 과 같게)
    """

    question: str
    input_type: str             # text · nameplate · damage · other
    question_type: str | None   # troubleshooting · maintenance · procedure · out_of_scope
    kb_id: str | None           # 근거 KB (kb/ 에 없는 id 면 근거 없이 기록)
    model: str | None           # 설비 모델명 (data/models.csv 표기)
    outcome: str
    repeat: int
    confidence: float | None = None   # 사진 판별 확신도
    safety: bool = True               # 답변에 안전 문구가 붙었는지
    review: tuple[bool, bool] | None = None   # 답 못 찾음 첫 기록의 (담당자 확인, KB 업데이트 필요)


DEMO_SCENARIOS: list[DemoScenario] = [
    DemoScenario("벨트가 꼬리 풀리 쪽에서 한쪽으로 쏠려요", "text", "troubleshooting", "TS-FENNERBELT-T01-01", None, "answered", 4),
    DemoScenario("벨트가 미끄러져요", "text", "troubleshooting", "TS-FENNERBELT-T01-07", None, "answered", 4),
    DemoScenario("138-ACC 컨베이어가 시동이 안 걸리고 모터가 자꾸 멈춰요", "text", "troubleshooting",
                 "TS-HYTROL138ACC-T01-01", "138-ACC", "answered", 3),
    DemoScenario("드르륵 갈리는 소리가 크게 나요", "text", "troubleshooting", "TS-HYTROL138ACC-T01-03", "138-ACC", "answered", 2),
    DemoScenario("구동부는 도는데 벨트가 안 움직여요", "text", "troubleshooting", "TS-HYTROL138ACC-T02-01", "138-LRC", "answered", 2),
    DemoScenario("L500 모터가 과열돼요", "text", "troubleshooting", "TS-PRECOLSERIES-T04-03", "L500", "answered", 3),
    DemoScenario("벨트 가장자리가 끊어졌어요", "damage", "troubleshooting", "TS-FENNERBELT-T01-19", None, "answered", 2,
                 confidence=0.84),
    DemoScenario("벨트 이음부가 벌어졌어요", "damage", "troubleshooting", "TS-FENNERBELT-T01-17", None, "answered", 2,
                 confidence=0.78),
    DemoScenario("138-ACC 드라이브 체인 장력은 어떻게 점검하나요?", "text", "maintenance",
                 "MT-HYTROL138ACC-T03-09", "138-ACC", "answered", 3),
    DemoScenario("L650 베어링 윤활은 언제 하나요?", "text", "maintenance", "MT-PRECOLSERIES-T02-04", "L650", "answered", 2),
    DemoScenario("감속기 온도는 얼마나 자주 확인하나요?", "text", "maintenance", "MT-HYTROL138ACC-T03-05", "138-ACC",
                 "answered", 2, safety=False),
    DemoScenario("(명판 사진)", "nameplate", None, None, "138-ACC", "nameplate", 3, confidence=0.93, safety=False),
    DemoScenario("(흐린 사진)", "other", None, None, None, "retake", 2, confidence=0.42, safety=False),
    DemoScenario("인버터 E07 에러가 떠요", "text", "troubleshooting", None, "L400", "not_found", 2, safety=False,
                 review=(True, True)),
    DemoScenario("롤러 베어링 교체 규격이 뭔가요?", "text", "maintenance", None, "138-ACC", "not_found", 1, safety=False,
                 review=(True, False)),
    DemoScenario("벨트 연결 클립은 어디서 사나요?", "text", "maintenance", None, None, "not_found", 1, safety=False),
    DemoScenario("오늘 점심 메뉴 추천해 주세요", "text", "out_of_scope", None, None, "out_of_scope", 1, safety=False),
    DemoScenario("모터에서 타는 냄새가 나요", "text", "troubleshooting", None, "138-ACC", "error", 1, safety=False),
]


def load_kb_ids(kb_dir: Path) -> set[str]:
    """kb/<종류>/*.json 에 있는 KB id 를 모은다. (데모 로그에 실제로 있는 KB 만 쓰려고)

    받는 것: KB 폴더 (Settings.kb_dir)
    돌려주는 것: kb_id 집합. 읽지 못한 파일은 건너뛴다.
    """
    kb_ids: set[str] = set()
    for path in sorted(Path(kb_dir).glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in data if isinstance(data, list) else [data]:
            if isinstance(entry, dict) and entry.get("kb_id"):
                kb_ids.add(entry["kb_id"])
    return kb_ids


def _demo_latency(rng: random.Random, outcome: str, has_photo: bool) -> dict[str, int]:
    """결과에 맞는 단계별 응답 시간(ms). 대시보드 시스템 탭의 p50·p95 계산에 쓰인다."""
    stages: dict[str, int] = {}
    if has_photo:
        stages["vision"] = rng.randint(1500, 3000)
    if outcome in ("answered", "not_found", "out_of_scope", "error"):
        stages["understand"] = rng.randint(600, 1200)
    if outcome in ("answered", "not_found", "error"):
        stages["search"] = rng.randint(400, 900)
    if outcome == "answered":
        stages["generate"] = rng.randint(1500, 4000)
        stages["verify"] = rng.randint(5, 40)
        stages["safety"] = rng.randint(1, 10)
    stages["total"] = sum(stages.values())
    return stages


def build_demo_logs(now: datetime, kb_ids: set[str]) -> list[dict]:
    """DEMO_SCENARIOS 로 데모 로그 행들을 만든다. (DB 를 쓰지 않는 계산만)

    받는 것: 지금 시각(데모 기간의 끝), kb/ 에 있는 KB id 집합
    돌려주는 것: [{"created_at", chat_logs 열(LOG_COLUMNS)..., "review": (확인, 업데이트 필요) 또는 None}]
      - 오늘을 포함한 최근 DEMO_DAYS 일, 09~18시 사이 (지금보다 늦은 시각은 만들지 않음)
      - 같은 날 다시 만들면 같은 결과 (DEMO_RANDOM_SEED + 날짜로 고정)
    """
    rng = random.Random(DEMO_RANDOM_SEED * 100_000 + now.date().toordinal())
    logs: list[dict] = []
    for scenario in DEMO_SCENARIOS:
        for index in range(scenario.repeat):
            day = now.date() - timedelta(days=rng.randrange(DEMO_DAYS))
            created_at = datetime.combine(day, datetime.min.time()) + timedelta(minutes=rng.randint(9 * 60, 18 * 60))
            if created_at > now:   # 오늘 아직 오지 않은 시각이면 지금보다 조금 앞으로
                created_at = now - timedelta(minutes=rng.randint(1, 120))
            answered = scenario.outcome == "answered"
            searched = scenario.outcome in ("answered", "not_found", "error")
            logs.append({
                "created_at": created_at.replace(second=0, microsecond=0),
                "source": "user",
                "employee_id": DEMO_EMPLOYEE_ID,
                "question": scenario.question,
                "input_type": scenario.input_type,
                "question_type": scenario.question_type,
                "equipment_model": scenario.model,
                "kb_ids": [scenario.kb_id] if answered and scenario.kb_id in kb_ids else [],
                "top_score": round(rng.uniform(0.62, 0.95) if answered or scenario.outcome == "error"
                                   else rng.uniform(0.25, 0.5), 3) if searched else None,
                "not_found": scenario.outcome == "not_found",
                "safety_included": answered and scenario.safety,
                "confidence": scenario.confidence,
                "latency_ms": _demo_latency(rng, scenario.outcome, scenario.input_type != "text"),
                "error": DEMO_ERROR if scenario.outcome == "error" else None,
                "review": scenario.review if index == 0 else None,
            })
    return sorted(logs, key=lambda log: log["created_at"])


def reset_demo_logs(conn: pymysql.connections.Connection, logs: list[dict]) -> tuple[int, int]:
    """데모 계정의 기존 로그를 지우고 새 데모 로그를 넣는다. (검토 상태 kb_gaps 포함)

    받는 것: DB 연결, build_demo_logs 결과
    돌려주는 것: (지운 로그 수, 넣은 로그 수)
    중간에 실패하면 main 이 rollback 해서 기존 데모 로그가 그대로 남는다.
    """
    columns = ("created_at", *LOG_COLUMNS)
    insert_sql = f"INSERT INTO chat_logs ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
    with conn.cursor() as cursor:
        # 검토 상태를 먼저 지운다 (예전 정의로 만든 kb_gaps 에는 ON DELETE CASCADE 가 없을 수 있어서)
        cursor.execute("DELETE g FROM kb_gaps g JOIN chat_logs l ON g.log_id = l.log_id WHERE l.employee_id = %s",
                       (DEMO_EMPLOYEE_ID,))
        deleted = cursor.execute("DELETE FROM chat_logs WHERE employee_id = %s", (DEMO_EMPLOYEE_ID,))
        for log in logs:
            values = tuple(json.dumps(log[c], ensure_ascii=False) if c in ("kb_ids", "latency_ms") else log[c]
                           for c in columns)
            cursor.execute(insert_sql, values)
            if log["review"] is not None:
                reviewed, kb_update_needed = log["review"]
                reviewed_at = min(log["created_at"] + timedelta(hours=20), datetime.now().replace(microsecond=0))
                cursor.execute(
                    "INSERT INTO kb_gaps (log_id, reviewed, kb_update_needed, reviewed_by, reviewed_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (cursor.lastrowid, reviewed, kb_update_needed, DEMO_REVIEWER_ID, reviewed_at),
                )
    conn.commit()
    return deleted, len(logs)
 
 
# ------------------------------------------------------------------------------
# 5. 전체 흐름
# ------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """seed 전체 과정을 실행한다.
 
    받는 것: 실행 옵션 (없으면 명령줄에서 읽음. --no-demo: 데모 로그를 넣지 않음)
    돌려주는 것: 종료 코드 (0 = 성공, 1 = 실패)
    """
    parser = argparse.ArgumentParser(description="테이블 만들기 + 초기 계정·데모 로그 넣기")
    parser.add_argument("--no-demo", action="store_true", help="데모 로그를 넣지 않음 (테이블·계정만)")
    args = parser.parse_args(argv)
 
    # 1) DB 에 접속하기 전에 계정 목록·설정·비밀번호를 먼저 검사한다. (실수를 일찍 잡기 위해)
    try:
        check_account_specs(DEFAULT_ACCOUNTS)
        settings = Settings.load()
        passwords = load_seed_passwords([spec.password_env for spec in DEFAULT_ACCOUNTS])
    except (SeedError, ConfigError) as error:
        print(error)
        return 1
    accounts = build_accounts(DEFAULT_ACCOUNTS, passwords)
 
    ssl_text = "SSL 사용" if settings.db_ssl_ca else "SSL 미사용"
    print(f"DB 접속 중: {settings.db_host}:{settings.db_port}/{settings.db_name} ({ssl_text})")
 
    # 2) DB 접속
    try:
        conn = connect(settings)
    except pymysql.MySQLError as error:
        print(f"DB 접속 실패: {error}")
        print("  → .env 의 DB_HOST·DB_PORT·DB_USER·DB_PASSWORD 와 certs/ca.pem 을 확인하고,"
              " Aiven 콘솔에서 서비스가 켜져(Running) 있는지 확인하세요.")
        return 1
 
    # 3) 테이블 만들기 → 계정 넣기. 중간에 실패하면 계정 작업은 전부 취소(rollback)한다.
    try:
        apply_schema(conn, SCHEMA_PATH)
        print(f"[OK] 테이블 확인 완료 ({SCHEMA_PATH.name})")
        for change in upgrade_users_table(conn):
            print(f"[OK] 예전 테이블 정의 변경: {change}")
        counts = upsert_accounts(conn, accounts)
        demo = None if args.no_demo else reset_demo_logs(conn, build_demo_logs(datetime.now(), load_kb_ids(settings.kb_dir)))
    except (pymysql.MySQLError, SeedError) as error:
        conn.rollback()
        print(f"[실패] 작업 중 오류가 나서 진행 중이던 변경을 취소했습니다: {error}")
        return 1
    finally:
        conn.close()   # 성공하든 실패하든 연결은 꼭 닫는다
 
    # 4) 결과 출력 (비밀번호는 출력하지 않는다)
    print(f"[OK] 계정 처리 완료: 새로 추가 {counts['added']} / 수정 {counts['updated']} / 그대로 {counts['unchanged']}")
    for account in accounts:
        print(f"  - {account.employee_id}  {account.name}  ({account.role})")
    if demo is not None:
        print(f"[OK] 데모 로그: 지운 {demo[0]} / 넣은 {demo[1]} (데모 계정 {DEMO_EMPLOYEE_ID}, 최근 {DEMO_DAYS}일)")
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())