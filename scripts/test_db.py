"""
==============================================================================
 test_db.py : MySQL(Aiven) 접속 점검  (담당: 이정빈 / WBS 3.1.4 · 5.1.6)
==============================================================================

[이 파일이 하는 일]
  .env 의 접속 정보로 DB 에 붙어서 아래 4가지를 확인하고 결과를 한 줄씩 보여 준다.
    1) 서버   : 접속 성공, MySQL 버전, 지금 시각, 시간대(+09:00)
    2) SSL    : 암호화 접속인지 (인증서를 설정했는데 SSL 이 아니면 실패)
    3) 테이블 : schema.sql 의 테이블이 모두 있는지, 문자셋이 utf8mb4 인지
    4) 계정   : users 에 계정이 들어 있는지 (seed.py 를 돌렸는지)
  읽기만 한다. 테이블을 만들거나 값을 바꾸지 않는다.

[언제 쓰나]
  - 처음 .env·ca.pem 을 채운 뒤
  - 발표 전날·당일 아침 Aiven 서비스가 켜져 있는지 확인할 때 (WBS 5.1.6)

[실행]  (프로젝트 최상단에서)
    python scripts/test_db.py
  모두 통과하면 종료 코드 0, 하나라도 실패하면 1.
==============================================================================
"""
import re
import sys
from pathlib import Path

# 이 파일은 scripts/ 안에 있어서, 그대로 두면 chatbot 패키지를 못 찾는다. → 최상단 폴더를 검색 경로 맨 앞에 넣는다.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from chatbot.core.config import ConfigError, Settings          # noqa: E402
from chatbot.db.db import TIME_ZONE, Database, DatabaseError    # noqa: E402

SCHEMA_PATH = PROJECT_ROOT / "chatbot" / "db" / "schema.sql"

# 점검 결과 한 건 = (상태, 내용). 상태: OK 통과 / WARN 주의(실패는 아님) / FAIL 실패
OK, WARN, FAIL = "OK", "주의", "실패"


def expected_tables(schema_path: Path = SCHEMA_PATH) -> list[str]:
    """schema.sql 에 정의된 테이블 이름을 순서대로 읽는다.

    받는 것: schema.sql 경로
    돌려주는 것: 테이블 이름 목록 (예: ["users", "sessions", ...])
    테이블 목록을 여기 따로 적지 않고 schema.sql 에서 읽어서, 테이블이 바뀌어도 이 파일은 고칠 필요가 없다.
    """
    sql = schema_path.read_text(encoding="utf-8-sig")
    sql = re.sub(r"--[^\n]*", "", sql)   # 주석 안의 "CREATE TABLE IF NOT EXISTS 를 쓴다" 같은 문장은 빼고 읽는다
    return re.findall(r"CREATE TABLE IF NOT EXISTS\s+`?(\w+)`?\s*\(", sql, flags=re.IGNORECASE)


def check_server(db: Database) -> list[tuple[str, str]]:
    """접속·버전·시각·시간대를 확인한다.

    받는 것: 연결된 Database
    돌려주는 것: [(상태, 내용)] 목록
    """
    row = db.fetch("SELECT VERSION() AS version, NOW() AS now, @@session.time_zone AS tz", ())[0]
    results = [(OK, f"접속 성공 · MySQL {row['version']} · 지금 시각 {row['now']:%Y-%m-%d %H:%M}")]
    if row["tz"] == TIME_ZONE:
        results.append((OK, f"시간대 {row['tz']} (한국 시간)"))
    else:
        results.append((FAIL, f"시간대가 {row['tz']} 입니다 → db.py 의 init_command(SET time_zone) 확인"))
    return results


def check_ssl(db: Database, settings: Settings) -> list[tuple[str, str]]:
    """SSL 로 접속했는지 확인한다.

    받는 것: 연결된 Database, Settings (인증서를 설정했는지 보려고)
    돌려주는 것: [(상태, 내용)] 목록
    """
    rows = db.fetch("SHOW SESSION STATUS LIKE 'Ssl_cipher'", ())
    cipher = rows[0]["Value"] if rows else ""
    if cipher:
        return [(OK, f"SSL 암호화 접속 ({cipher})")]
    if settings.db_ssl_ca:
        return [(FAIL, "인증서를 설정했는데 SSL 로 접속되지 않았습니다 → DB_SSL_CA·certs/ca.pem 확인")]
    return [(WARN, "SSL 없이 접속 중입니다 (DB_SSL_CA 가 비어 있음 · 로컬 MySQL 백업용일 때만 정상)")]


def check_tables(db: Database, tables: list[str]) -> list[tuple[str, str]]:
    """schema.sql 의 테이블이 모두 있고 문자셋이 utf8mb4 인지 확인한다.

    받는 것: 연결된 Database, 있어야 할 테이블 이름 목록
    돌려주는 것: [(상태, 내용)] 목록
    """
    rows = db.fetch(
        "SELECT TABLE_NAME AS name, TABLE_COLLATION AS collation "
        "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()",
        (),
    )
    found = {row["name"]: row["collation"] or "" for row in rows}

    missing = [t for t in tables if t not in found]
    if missing:
        return [(FAIL, f"테이블 {len(missing)}개가 없습니다: {', '.join(missing)} → python scripts/seed.py 실행")]

    results = [(OK, f"테이블 {len(tables)}개 모두 있음: {', '.join(tables)}")]
    not_utf8 = [t for t in tables if not found[t].startswith("utf8mb4")]
    if not_utf8:
        results.append((FAIL, f"utf8mb4 가 아닌 테이블: {', '.join(not_utf8)} → 한글이 깨질 수 있음"))
    extra = sorted(set(found) - set(tables))
    if extra:
        results.append((WARN, f"schema.sql 에 없는 테이블이 있습니다: {', '.join(extra)}"))
    return results


def check_accounts(db: Database) -> list[tuple[str, str]]:
    """users 에 계정이 들어 있는지 권한별로 센다.

    받는 것: 연결된 Database
    돌려주는 것: [(상태, 내용)] 목록
    """
    rows = db.fetch("SELECT role, COUNT(*) AS cnt FROM users GROUP BY role", ())
    counts = {row["role"]: row["cnt"] for row in rows}
    if not counts.get("admin"):
        return [(FAIL, "관리자 계정이 없습니다 → python scripts/seed.py 실행")]
    return [(OK, f"계정: 관리자 {counts.get('admin', 0)}명 · 사원 {counts.get('user', 0)}명")]


def run_checks(db: Database, settings: Settings, tables: list[str]) -> list[tuple[str, str]]:
    """점검 4가지를 순서대로 실행한다. 테이블이 없으면 계정 점검은 건너뛴다.

    받는 것: 연결된 Database, Settings, 있어야 할 테이블 목록
    돌려주는 것: 모든 [(상태, 내용)] 목록
    """
    results = check_server(db) + check_ssl(db, settings)
    table_results = check_tables(db, tables)
    results += table_results
    if "users" in tables and all(state != FAIL for state, _ in table_results):
        results += check_accounts(db)
    return results


def main() -> int:
    """설정을 읽고 DB 에 접속해 점검 결과를 출력한다.

    받는 것: 없음
    돌려주는 것: 종료 코드 (0 = 모두 통과, 1 = 실패 있음)
    """
    try:
        settings = Settings.load()
    except ConfigError as error:
        print(error)
        return 1

    ssl_text = "SSL" if settings.db_ssl_ca else "SSL 없음"
    print(f"DB 점검: {settings.db_host}:{settings.db_port}/{settings.db_name} ({ssl_text})")
    try:
        db = Database().connect(settings)
    except DatabaseError as error:
        print(f"  [{FAIL}] {error}")
        return 1

    try:
        results = run_checks(db, settings, expected_tables())
    finally:
        db.close()

    for state, message in results:
        print(f"  [{state}] {message}")
    failed = sum(1 for state, _ in results if state == FAIL)
    print("결과: 모두 통과" if failed == 0 else f"결과: 실패 {failed}건")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
