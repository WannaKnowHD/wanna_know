"""
==============================================================================
 config.py : 환경 설정 모듈  (담당: 이정빈)
==============================================================================
 
[이 파일이 하는 일]
  .env 파일(또는 시스템 환경변수)에 적힌 값을 읽어서 Settings 객체 하나로 모아 준다.
  값이 비었거나 잘못되면, 무엇이 문제이고 "어디서 값을 구하는지"까지 한 번에 알려 준다.
 
[팀원 사용 규칙]
  - 다른 파일에서는 os.getenv(...) 로 직접 읽지 말고 항상 Settings.load() 를 쓴다.
    (설정을 읽는 곳이 한 군데여야 이름을 바꿀 때 이 파일만 고치면 된다)
  - 코드에 API 키·모델명·비밀번호를 직접 쓰지 않는다. (기능명세서 config.py 행)
 
[이 파일이 제공하는 것]
  - Settings.load()        : 앱 전체 설정. 필수 값을 모두 검사한다. (앱·팀원 코드용)
  - load_openai_settings() : OpenAI 키·모델명만 읽는다. (scripts/check_models.py 전용)
                             → 모델 이름을 아직 모르는 상태에서도 모델 확인을 할 수 있게 하려는 것
  - ConfigError            : 설정이 잘못됐을 때 나는 오류
 
[사용 예]
    from config import Settings
    settings = Settings.load()      # 앱이 시작될 때 한 번만 부른다
    print(settings.model_text)
 
[직접 실행해서 점검]
    python config.py
==============================================================================
"""
# 타입 표기(Optional[Path], list[str] 등)를 실행할 때 따지지 않게 한다.
# 팀원마다 파이썬 버전이 조금 달라도 타입 표기 때문에 깨지지 않게 하는 보험이다.
from __future__ import annotations
 
import os                                   # 환경변수를 읽는 기본 도구
from dataclasses import dataclass, field, fields   # 설정을 담는 "그릇"(클래스)을 쉽게 만드는 도구
from pathlib import Path                    # Windows(\) / Mac(/) 경로 차이를 알아서 처리해 주는 도구
from typing import Callable, Optional, TypeVar  # 타입 표기 도구 (Optional: 값이 없을 수도 있음(None))
 
from dotenv import load_dotenv              # .env 파일 내용을 환경변수로 올려 주는 도구 (pip install python-dotenv)
 
 
# ------------------------------------------------------------------------------
# 1. 기준 경로
# ------------------------------------------------------------------------------
# __file__        : 지금 이 파일(config.py)의 위치
# .resolve()      : 전체 경로로 바꿈 (예: C:/Users/.../project/config.py)
# .parent         : 그 파일이 들어 있는 폴더 = 프로젝트 최상단 폴더
# 그냥 ".env" 라고 쓰면 "터미널이 지금 있는 폴더" 기준으로 찾기 때문에,
# 다른 폴더에서 실행하면 못 찾는다. BASE_DIR 기준이면 어디서 실행해도 같은 파일을 찾는다.
# ※ config.py 를 src/ 같은 하위 폴더로 옮기면 .parent 를 .parent.parent 로 바꿔야 한다.
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
 
 
# ------------------------------------------------------------------------------
# 2. 값이 비었을 때 보여 줄 "해결 방법" 안내
# ------------------------------------------------------------------------------
# 오류 줄마다 "→ 어디서 값을 구하는지"를 붙여 주기 위한 사전(dict)이다.
# 키: 환경변수 이름 / 값: 해결 방법
FIX_HINTS: dict[str, str] = {
    "OPENAI_API_KEY": "OpenAI 대시보드(platform.openai.com) > API keys 에서 발급한 키를 붙여 넣기",
    "MODEL_TEXT": "python scripts/check_models.py 실행 → '텍스트·사진 후보' 중 텍스트 테스트를 통과한 이름 적기",
    "MODEL_VISION": "python scripts/check_models.py 실행 → 사진 테스트를 통과한 이름 적기",
    "MODEL_EMBED": "python scripts/check_models.py 실행 → '임베딩 후보'에서 고르기 (보통 text-embedding-3-small)",
    "DB_HOST": "Aiven 콘솔 > MySQL 서비스 > Overview > Connection information 의 Host",
    "DB_PORT": "같은 화면의 Port (숫자)",
    "DB_USER": "같은 화면의 User (보통 avnadmin)",
    "DB_PASSWORD": "같은 화면의 Password",
    "DB_NAME": "같은 화면의 Database name (보통 defaultdb)",
    "DB_SSL_CA": "같은 화면의 CA certificate 를 내려받아 프로젝트 최상단 certs/ca.pem 으로 저장 "
                 "(로컬 MySQL처럼 SSL을 안 쓰면 .env 에 DB_SSL_CA= 처럼 값을 비워 두기)",
    "BROWSER_STORAGE_KEY": "아무 고정 이름이면 됨 (예: wanna_know_session)",
    "BROWSER_SECRET_KEY": '터미널에서 python -c "import secrets; print(secrets.token_hex(32))" 실행 → 나온 값 붙여 넣기',
}
 
 
# ------------------------------------------------------------------------------
# 3. 설정 오류 전용 에러
# ------------------------------------------------------------------------------
class ConfigError(RuntimeError):
    """환경 설정이 잘못되었을 때 발생하는 오류 (누락·형식 오류·파일 없음).
 
    전용 에러를 따로 만든 이유:
      - 화면 쪽 코드가 except ConfigError 로 "설정 문제"만 골라 잡아 안내할 수 있다.
      - 함수 이름 오타(NameError) 같은 "코드 문제"는 섞이지 않고 그대로 드러난다.
    """
 
 
# ------------------------------------------------------------------------------
# 4. 도우미 함수들 (이름 앞 _ : 이 파일 안에서만 쓰는 함수라는 파이썬 관례)
# ------------------------------------------------------------------------------
def _load_env_file() -> Optional[str]:
    """프로젝트 최상단의 .env 파일을 읽어 환경변수로 올린다.
 
    받는 것: 없음
    돌려주는 것: .env 파일이 없으면 그 사실을 알리는 안내 문구, 있으면 None
    """
    if ENV_PATH.exists():
        # encoding="utf-8-sig" : Windows 메모장이 파일 맨 앞에 붙이는 보이지 않는 글자(BOM)가 있어도 안전하게 읽는다.
        # 이미 시스템에 설정된 환경변수는 덮어쓰지 않는다(기본 동작).
        #   → 나중에 배포 서버에 Secrets 로 넣은 값이 .env 보다 우선한다.
        load_dotenv(ENV_PATH, encoding="utf-8-sig")
        return None
 
    # .env 가 없을 때: Windows 에서 흔한 실수(메모장이 .env.txt 로 저장)까지 확인한다.
    if (BASE_DIR / ".env.txt").exists():
        return (".env 파일 이름이 .env.txt 로 저장되어 있습니다 → 파일 이름을 .env 로 바꾸기 "
                "(메모장에서 저장할 때 '파일 형식: 모든 파일' 선택)")
    return f".env 파일이 없습니다 ({ENV_PATH}) → .env.example 을 복사해 .env 를 만들고 값 채우기"
 
 
def _problem_line(name: str, message: str) -> str:
    """문제 한 줄을 "문제 → 해결 방법" 형식으로 만든다.
 
    받는 것: 환경변수 이름, 문제 설명
    돌려주는 것: 안내 문구가 붙은 한 줄 (안내가 없으면 문제 설명만)
    """
    hint = FIX_HINTS.get(name)
    return f"{message} → {hint}" if hint else message
 
 
def _required(name: str, problems: list[str]) -> str:
    """반드시 있어야 하는 환경변수를 문자열로 읽는다.
 
    받는 것: 환경변수 이름, 문제 목록(problems)
    돌려주는 것: 값(앞뒤 공백 제거). 비어 있으면 problems 에 한 줄 기록하고 빈 문자열을 돌려준다.
 
    바로 멈추지 않고 problems 에 적어 두는 이유:
      빈 항목이 여러 개일 때 "고치고 실행"을 여러 번 반복하지 않고 한 번에 고칠 수 있게 하려고.
    """
    # .strip() : "DB_USER= avnadmin " 처럼 실수로 들어간 앞뒤 공백을 지운다.
    value = os.getenv(name, "").strip()
    if not value:
        problems.append(_problem_line(name, f"{name} 값이 비어 있습니다"))
    return value
 
 
# NumberT : "int 또는 float 중 하나"를 뜻하는 타입 변수.
# _number 에 int 를 넣으면 int 가, float 를 넣으면 float 가 돌아온다는 것을 VS Code 검사기에게 알려 준다.
# (이 표기가 없으면 검사기가 돌려주는 값이 숫자인지조차 몰라서 빨간 줄을 띄운다)
NumberT = TypeVar("NumberT", int, float)
 
 
def _number(
    name: str,
    default: NumberT,
    cast: Callable[[str], NumberT],
    problems: list[str],
    min_value: Optional[NumberT] = None,
    max_value: Optional[NumberT] = None,
    required: bool = False,
) -> NumberT:
    """숫자 환경변수를 읽는다.
 
    받는 것:
      name      : 환경변수 이름
      default   : 값이 없을 때 쓸 기본값
      cast      : 변환 함수. int 를 넣으면 정수, float 를 넣으면 소수로 바꾼다.
      problems  : 문제 목록
      min_value, max_value : 허용 범위 (선택). 예) 기준값은 0.0 ~ 1.0
      required  : True 면 값이 비었을 때 기본값 대신 문제로 기록한다.
    돌려주는 것:
      변환된 숫자. 문제가 있으면 problems 에 기록하고 default 를 돌려준다.
      (default 를 돌려주는 이유: 나머지 항목 검사를 끝까지 계속하기 위해. 문제는 이미 기록돼 있다)
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        if required:
            problems.append(_problem_line(name, f"{name} 값이 비어 있습니다"))
        return default
 
    try:
        value = cast(raw)               # 예) int("12345") → 12345, float("0.55") → 0.55
    except ValueError:                  # 예) int("abc") 처럼 숫자로 못 바꾸면 ValueError 가 난다
        problems.append(_problem_line(name, f"{name} 값은 숫자여야 합니다 (현재: {raw})"))
        return default
 
    # 범위 검사: 기준값 0.55 를 실수로 55 라고 적으면 모든 답변이 "확인되지 않음"이 되는데,
    # 원인을 찾기 어렵다. 실행하자마자 알려 주기 위해 범위를 확인한다.
    too_small = min_value is not None and value < min_value
    too_big = max_value is not None and value > max_value
    if too_small or too_big:
        problems.append(f"{name} 값은 {min_value}~{max_value} 범위여야 합니다 (현재: {value})")
        return default
    return value
 
 
def _ssl_ca_path(problems: list[str]) -> Optional[Path]:
    """MySQL SSL 인증서(ca.pem) 경로를 읽고, 파일이 실제로 있는지 확인한다.
 
    받는 것: 문제 목록
    돌려주는 것: 인증서 경로(Path). .env 에 DB_SSL_CA= 처럼 비워 두면 None (SSL 없이 접속)
    """
    # 환경변수가 아예 없으면 기본값 certs/ca.pem (기능명세서 그대로)
    # 환경변수가 "있는데 비어 있으면" 빈 문자열 → SSL 사용 안 함 (기획안 8번: 로컬 MySQL 백업 시연용)
    raw = os.getenv("DB_SSL_CA", "certs/ca.pem").strip()
    if not raw:
        return None
 
    path = Path(raw)
    if not path.is_absolute():          # 상대 경로면 프로젝트 최상단 기준으로 바꾼다
        path = BASE_DIR / path
    if not path.exists():
        # 지금 확인하는 이유: 나중에 db.py 에서 알아보기 힘든 SSL 오류로 터지기 전에 미리 알려 주려고
        problems.append(_problem_line("DB_SSL_CA", f"DB_SSL_CA 인증서 파일을 찾을 수 없습니다 ({path})"))
    return path
 
 
def _raise_if_problems(problems: list[str], env_note: Optional[str]) -> None:
    """문제가 하나라도 있으면 전부 모아서 ConfigError 를 낸다.
 
    받는 것: 문제 목록, .env 파일 관련 안내(없으면 None)
    돌려주는 것: 없음 (문제가 없으면 아무 일도 하지 않음)
    """
    if not problems:
        return
    # .env 파일 자체가 없다면 그게 가장 근본 원인이므로 맨 앞에 보여 준다.
    # (문제가 없을 때는 .env 가 없어도 괜찮다. 배포 서버처럼 시스템 환경변수로 값을 넣는 경우)
    if env_note:
        problems.insert(0, env_note)
    numbered = "\n".join(f"  {i}. {line}" for i, line in enumerate(problems, start=1))
    raise ConfigError(f"환경 설정 오류 {len(problems)}건: 아래 항목을 .env 에서 고쳐 주세요\n{numbered}")
 
 
# ------------------------------------------------------------------------------
# 5. 앱 전체 설정 (Settings)
# ------------------------------------------------------------------------------
# @dataclass         : 아래 적은 항목들로 클래스를 자동으로 만들어 준다.
# frozen=True        : 한번 만든 뒤에는 값을 바꿀 수 없다.
#                      (누가 실행 중에 기준값을 바꿔서 다른 사람 기능 결과까지 달라지는 사고 방지)
# field(repr=False)  : print(settings) 를 해도 이 값은 출력되지 않는다.
#                      (화면 캡처·시연 영상·팀 채팅에 키·비밀번호가 새는 것 방지)
# ※ dataclass 규칙: 기본값이 있는 항목(아래 경로들)은 기본값이 없는 항목보다 뒤에 와야 한다.
#   순서가 바뀌면 TypeError: non-default argument follows default argument 가 난다.
@dataclass(frozen=True)
class Settings:
    """프로젝트 전체에서 쓰는 설정 값 묶음. Settings.load() 로 만든다."""
 
    # --- OpenAI (박세람 llm.py 에서 사용) ---
    openai_api_key: str = field(repr=False)   # API 키 (비밀)
    model_text: str                            # 텍스트 답변용 모델 이름 (환경변수 MODEL_TEXT)
    model_vision: str                          # 사진(명판·손상) 분석용 모델 이름 (환경변수 MODEL_VISION)
    model_embed: str                           # 임베딩 모델 이름 (환경변수 MODEL_EMBED)
 
    # --- MySQL / Aiven (공통 db.py 의 Database.connect(settings) 에서 사용) ---
    db_host: str
    db_port: int
    db_user: str
    db_password: str = field(repr=False)       # DB 비밀번호 (비밀)
    db_name: str
    db_ssl_ca: Optional[Path]                  # 인증서(ca.pem) 경로. SSL 안 쓰면 None
 
    # --- 로그인 세션 (김현준 auth.py · gradio_app.py 의 gr.BrowserState 에서 사용) ---
    browser_storage_key: str                   # 브라우저에 토큰을 저장할 때 쓰는 고정 이름
    browser_secret_key: str = field(repr=False)  # 브라우저 저장 값을 암호화하는 키 (비밀)
    session_ttl_hours: int                     # 로그인 유지 시간 (기본 8시간)
 
    # --- 기준값 ---
    evidence_score_threshold: float            # 근거 점수가 이보다 낮으면 "확인되지 않음" (조윤빈, 기본 0.55)
    photo_confidence_threshold: float          # 사진 확신도가 이보다 낮으면 재촬영 요청 (김민재, 기본 0.6)
 
    # --- 문서·색인 경로 (모두 프로젝트 최상단 기준) ---
    # 사람마다 다를 필요가 없는 값이라 .env 에 넣지 않고 고정값으로 둔다. (설정할 항목을 줄여 실수 감소)
    # ※ 폴더 구조도 확정본과 다르면 이 값만 고치면 된다.
    raw_dir: Path = BASE_DIR / "data" / "raw"                   # 원본 매뉴얼 PDF
    admin_add_dir: Path = BASE_DIR / "data" / "raw" / "admin_add"  # 관리자가 화면에서 추가한 PDF
    interim_dir: Path = BASE_DIR / "data" / "interim"           # 표·그림 잘라낸 이미지와 변환 결과
    kb_dir: Path = BASE_DIR / "kb"                              # 지식베이스(JSON)
    chroma_dir: Path = BASE_DIR / "chroma_db"                   # 벡터 DB 폴더 (.gitignore 대상)
    bm25_path: Path = BASE_DIR / "chroma_db" / "bm25.pkl"       # BM25 색인 파일 (강경원과 위치 확정 필요)
    results_dir: Path = BASE_DIR / "results"                    # 평가 결과 CSV
 
    # @classmethod : 객체가 아직 없어도 Settings.load() 처럼 클래스 이름으로 바로 부를 수 있게 한다.
    #                cls 는 Settings 클래스 자신을 가리킨다.
    @classmethod
    def load(cls) -> "Settings":
        """.env 와 환경변수에서 설정을 읽어 Settings 로 만든다.
 
        받는 것: 없음
        돌려주는 것: Settings
        문제가 있으면 끝까지 다 검사한 뒤 한 번에 모아서 ConfigError 로 알려 준다.
        """
        env_note = _load_env_file()       # 1) .env 를 환경변수로 올린다
        problems: list[str] = []          # 2) 문제를 모아 둘 빈 목록
 
        # 3) 모든 값을 읽으면서 바로 Settings 를 만든다. (읽는 도중 문제가 생기면 problems 에 쌓인다)
        #    파이썬은 괄호 안의 인자를 "전부" 먼저 계산한 뒤에 Settings 를 만들기 때문에,
        #    여기서 이미 모든 항목 검사가 끝난다. (문제를 한 번에 모으는 동작은 그대로)
        #    ※ 예전처럼 dict 에 담았다가 cls(**values) 로 펼치면, VS Code 검사기가
        #      "어느 칸에 어떤 타입이 들어 있는지" 추적하지 못해 칸마다 빨간 줄을 띄웠다.
        settings = cls(
            openai_api_key=_required("OPENAI_API_KEY", problems),
            model_text=_required("MODEL_TEXT", problems),
            model_vision=_required("MODEL_VISION", problems),
            model_embed=_required("MODEL_EMBED", problems),
            db_host=_required("DB_HOST", problems),
            # 포트는 Aiven 서비스마다 다르다. 기본값(3306)을 깔면 엉뚱한 곳에 접속하므로 반드시 적게 한다.
            # 기본값 0 은 "비어 있음" 표시용이다. 비어 있으면 required=True 라서 문제로 기록되고,
            # 아래 4)에서 멈추므로 0 이 실제로 쓰이는 일은 없다. (None 대신 0: 타입을 int 로 맞추기 위해)
            db_port=_number("DB_PORT", 0, int, problems, 1, 65535, required=True),
            db_user=_required("DB_USER", problems),
            db_password=_required("DB_PASSWORD", problems),
            db_name=_required("DB_NAME", problems),
            db_ssl_ca=_ssl_ca_path(problems),
            browser_storage_key=_required("BROWSER_STORAGE_KEY", problems),
            browser_secret_key=_required("BROWSER_SECRET_KEY", problems),
            session_ttl_hours=_number("SESSION_TTL_HOURS", 8, int, problems, 1, 168),
            evidence_score_threshold=_number("EVIDENCE_SCORE_THRESHOLD", 0.55, float, problems, 0.0, 1.0),
            photo_confidence_threshold=_number("PHOTO_CONFIDENCE_THRESHOLD", 0.6, float, problems, 0.0, 1.0),
        )
 
        # 4) 문제가 있으면 여기서 멈추고 한 번에 알려 준다. (빈 값이 든 settings 는 버려진다)
        _raise_if_problems(problems, env_note)
 
        # 5) 문제가 없으면 완성된 Settings 를 돌려준다.
        return settings
 
 
# ------------------------------------------------------------------------------
# 6. OpenAI 설정만 따로 읽기 (scripts/check_models.py 전용)
# ------------------------------------------------------------------------------
# 이 부분이 없으면 생기는 문제(실제로 겪은 문제):
#   check_models.py 는 "어떤 모델 이름을 MODEL_TEXT 에 적을지" 알아내는 도구인데,
#   Settings.load() 를 쓰면 MODEL_TEXT·MODEL_VISION 이 이미 채워져 있어야 하고,
#   모델 확인과 상관없는 DB 정보·인증서·암호 키까지 요구해서 실행조차 못 했다.
#   → OpenAI 키만 있으면 실행되도록 따로 읽는 함수를 둔다.
@dataclass(frozen=True)
class OpenAISettings:
    """모델 확인용 OpenAI 설정. 모델 이름은 아직 비어 있어도("") 된다."""
 
    api_key: str = field(repr=False)
    model_text: str
    model_vision: str
    model_embed: str
 
 
def load_openai_settings() -> OpenAISettings:
    """OpenAI 키와 모델 이름만 읽는다. (scripts/check_models.py 전용)
 
    받는 것: 없음
    돌려주는 것: OpenAISettings (OPENAI_API_KEY 만 필수, 모델 이름은 비어 있으면 "")
    """
    env_note = _load_env_file()
    problems: list[str] = []
    api_key = _required("OPENAI_API_KEY", problems)
    _raise_if_problems(problems, env_note)
    return OpenAISettings(
        api_key=api_key,
        model_text=os.getenv("MODEL_TEXT", "").strip(),
        model_vision=os.getenv("MODEL_VISION", "").strip(),
        model_embed=os.getenv("MODEL_EMBED", "").strip(),
    )
 
 
# ------------------------------------------------------------------------------
# 7. 직접 실행했을 때: 설정 점검 (python config.py)
# ------------------------------------------------------------------------------
# if __name__ == "__main__": 아래는 "python config.py" 로 직접 실행할 때만 동작한다.
# 다른 파일이 import config 할 때는 실행되지 않는다.
if __name__ == "__main__":
    try:
        settings = Settings.load()
    except ConfigError as error:
        # 긴 오류 화면(Traceback) 대신 안내 문구만 보여 주고 종료한다. (종료 코드 1 = 실패)
        raise SystemExit(str(error))
 
    print("[OK] 설정을 모두 읽었습니다. (비밀 값은 표시하지 않음)")
    for item in fields(settings):
        if item.repr:                     # repr=False 인 비밀 값은 건너뛴다
            print(f"  {item.name} = {getattr(settings, item.name)}")