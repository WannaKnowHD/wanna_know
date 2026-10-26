"""
==============================================================================
 scripts/seed.py : 테이블 만들기 + 초기 계정 넣기  (담당: 이정빈 / WBS 3.2.1 · 5.1.1 / 기능 A-01)
==============================================================================
 
[이 파일이 하는 일]
  1) .env 의 DB 접속 정보로 MySQL(Aiven)에 접속한다.            (config.Settings.load)
  2) .env 에서 초기 계정 비밀번호를 읽는다.                       (config.load_seed_passwords)
  3) db/schema.sql 을 실행해 테이블을 만든다.                     (이미 있으면 건너뜀)
  4) 아래 DEFAULT_ACCOUNTS 의 계정을 users 테이블에 넣는다.        (비밀번호는 해시로 바꿔서)
 
[비밀번호는 어디에?]
  이 파일에는 사원번호·이름·권한과 "비밀번호를 읽을 .env 항목 이름"만 적는다.
  비밀번호 원문은 .env(Git 제외)에만 있다. → GitHub·제출 zip 에 비밀번호가 남지 않는다.
 
[여러 번 실행해도 안전한가]  예. (WBS 5.1.1)
  - 테이블은 CREATE TABLE IF NOT EXISTS 라서 기존 데이터를 지우지 않는다.
  - 같은 사원번호가 있으면 새로 넣지 않고 이름·권한·비밀번호를 고친다. (중복 계정 없음)
  - 바뀐 게 없는 계정은 건드리지 않는다.
  - 아래 목록에 없는 기존 계정은 지우지 않는다.
 
[실행 전 준비]
  1) .env 가 모두 채워져 있어야 한다. (python config.py 가 [OK])
  2) .env 에 아래 두 줄 추가 (각자 8자 이상으로 정하기)
       SEED_ADMIN_PASSWORD=...
       SEED_USER_PASSWORD=...
  3) pip install "PyMySQL[rsa]"
 
[실행]  프로젝트 최상단 폴더에서, 가상환경을 켠 상태로
  python scripts/seed.py
==============================================================================
"""
import hashlib   # 비밀번호 해시 계산 (PBKDF2)
import hmac      # 두 해시를 안전하게 비교 (compare_digest)
import secrets   # 예측할 수 없는 무작위 값 생성 (소금값)
import sys
from dataclasses import dataclass, field
from pathlib import Path
 
# 이 파일은 scripts/ 안에 있어서, 그대로 두면 최상단의 config.py 를 못 찾는다.
# → 최상단 폴더(scripts 의 부모)를 파이썬 검색 경로 맨 앞에 넣는다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
 
import pymysql                              # noqa: E402  MySQL 접속 라이브러리
from pymysql.constants import CLIENT        # noqa: E402  접속 옵션 상수
 
from chatbot.core.config import BASE_DIR, ConfigError, Settings, load_seed_passwords   # noqa: E402
 
 
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
]
 
 
# ------------------------------------------------------------------------------
# 2. 고정값
# ------------------------------------------------------------------------------
SCHEMA_PATH = BASE_DIR / "chatbot" / "db" / "schema.sql"   # 테이블 만드는 SQL
ALLOWED_ROLES = ("user", "admin")              # users.role 에 들어갈 수 있는 값
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
# 5. 전체 흐름
# ------------------------------------------------------------------------------
def main() -> int:
    """seed 전체 과정을 실행한다.
 
    받는 것: 없음
    돌려주는 것: 종료 코드 (0 = 성공, 1 = 실패)
    """
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
        counts = upsert_accounts(conn, accounts)
    except (pymysql.MySQLError, SeedError) as error:
        conn.rollback()
        print(f"[실패] 작업 중 오류가 나서 계정 변경을 취소했습니다: {error}")
        return 1
    finally:
        conn.close()   # 성공하든 실패하든 연결은 꼭 닫는다
 
    # 4) 결과 출력 (비밀번호는 출력하지 않는다)
    print(f"[OK] 계정 처리 완료: 새로 추가 {counts['added']} / 수정 {counts['updated']} / 그대로 {counts['unchanged']}")
    for account in accounts:
        print(f"  - {account.employee_id}  {account.name}  ({account.role})")
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())