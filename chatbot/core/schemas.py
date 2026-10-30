"""공통 데이터 클래스. 여러 파일이 같이 쓰는 타입을 한곳에 모아 순환 import를 막음."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


@dataclass(frozen=True)
class User:
    """로그인한 사원. 값 변경 불가, 비밀번호 해시는 넣지 않음."""
    employee_id: str
    name: str
    role: str  # "user" | "admin"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass
class Page:
    """PDF 한 페이지 추출 결과."""
    doc_id: str
    page_no: int
    text: str
    regions: list[dict] = field(default_factory=list)  # 표·그림 종류와 bbox


@dataclass
class Section:
    """섹션 단위 본문."""
    doc_id: str
    section_path: str
    page: int
    type: str  # "text" | "table" | "figure"
    text: str
    image_path: str | None = None


@dataclass
class Chunk:
    """색인 단위 청크. text는 검색 대상, 나머지는 메타데이터."""
    chunk_id: str
    doc_id: str
    text: str
    chunk_type: str
    section_path: str
    page: int
    equipment_model: str | None = None
    image_path: str | None = None
    safety_flag: bool = False


@dataclass
class Hit:
    """검색 결과 1건."""
    id: str  # chunk_id 또는 kb_id
    text: str
    score: float
    doc_id: str
    page: int
    metadata: dict = field(default_factory=dict)


@dataclass
class QueryInfo:
    """질문 이해 결과."""
    qtype: str  # troubleshooting | maintenance | procedure | out_of_scope
    equipment_hint: str | None = None
    part_hint: str | None = None
    symptom: str | None = None
    queries: list[str] = field(default_factory=list)


@dataclass
class Model:
    """설비 모델 정보 (models.csv 한 줄)."""
    model_name: str
    aliases: list[str] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)


# KB 스키마 (kb/troubleshooting/*.json, kb/maintenance/*.json)
# KB 클래스는 모두 extra="forbid": 정의에 없는 필드(오타 등)가 들어오면 값을 버리지 않고 오류로 알림

# 문서코드 = sources.csv의 doc_id를 대문자로 바꾸고 "_"를 뺀 값 (hytrol_138acc → HYTROL138ACC).
# csv에 컬럼을 따로 두지 않아도 doc_id에서 바로 만들 수 있고, doc_id와 1:1로 대응한다.
KB_ID_PATTERN = r"^(TS|MT)-[A-Z0-9]+-T\d{2}-\d{2}$"   # 예: TS-HYTROL138ACC-T01-01

# applies_to에 이 값만 적으면 "모든 설비 모델에 해당". 벨트 매뉴얼(fenner_belt)처럼 특정 기종이 없는 문서용.
# 설비 필터를 걸어도 이 KB는 항상 검색 대상에 남아야 한다 (Retriever·청킹에서 같은 상수를 씀).
ALL_MODELS = "*"


class SourceRef(BaseModel):
    """KB 내용의 출처 (매뉴얼의 어느 문서·쪽·표/절).
    판(version)은 두지 않음: sources.csv에 문서마다 판이 하나뿐이라 doc_id로 구분된다."""
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    page: int = Field(ge=1)  # 매뉴얼 하단에 인쇄된 쪽 번호 (sources.csv page_map 으로 맞춤). 잘라 낸 파일 쪽 아님
    ref: str  # "Table 8", "Section 4.1"


class FigureRef(BaseModel):
    """KB와 관련된 그림. image_path는 crop_regions가 잘라 둔 이미지 경로 (색인 시 채움)."""
    model_config = ConfigDict(extra="forbid")
    figure_ref: str  # "Figure 5"
    page: int = Field(ge=1)  # SourceRef.page와 같은 기준 (원본 PDF 쪽)
    image_path: str | None = None


class KbCause(BaseModel):
    """트러블슈팅 KB의 원인 1개와 조치. cause_id = {kb_id}#c{순번}."""
    model_config = ConfigDict(extra="forbid")
    cause_id: str
    cause: str
    actions: list[str] = Field(min_length=1)


class _KbBase(BaseModel):
    model_config = ConfigDict(extra="forbid")  # TroubleshootingKB·MaintenanceKB가 물려받음
    kb_id: str = Field(pattern=KB_ID_PATTERN)
    applies_to: list[str] = Field(min_length=1)  # data/models.csv의 모델명과 같은 표기
    sources: list[SourceRef] = Field(min_length=1)
    figures: list[FigureRef] = []
    related_kb: list[str] = []

    @field_validator("applies_to")
    @classmethod
    def _check_all_models(cls, v: list[str]) -> list[str]:
        # ["*", "138-ACC"]처럼 섞어 쓰면 "모든 모델"인지 "일부 모델"인지 애매해지므로 막는다.
        if ALL_MODELS in v and len(v) > 1:
            raise ValueError(f'applies_to에 "{ALL_MODELS}"를 쓰면 다른 모델명과 함께 적을 수 없음')
        return v


class TroubleshootingKB(_KbBase):
    """증상 → 원인 → 조치. 매뉴얼의 트러블슈팅 표·알람 코드 표에서 만듦."""
    type: Literal["troubleshooting"]
    symptom: str
    alarm_codes: list[str] = []
    causes: list[KbCause] = Field(min_length=1)
    safety: list[str] = []

    @model_validator(mode="after")
    def _check_ids(self):
        if not self.kb_id.startswith("TS-"):
            raise ValueError("트러블슈팅 KB의 kb_id는 TS-로 시작해야 함")
        for i, c in enumerate(self.causes, start=1):
            expected = f"{self.kb_id}#c{i}"
            if c.cause_id != expected:
                raise ValueError(f"cause_id는 원본 표 순서대로 {expected}이어야 함 (현재 {c.cause_id})")
        return self


class MaintenanceKB(_KbBase):
    """점검·정비 작업과 주기. 매뉴얼의 점검표·윤활표에서 만듦."""
    type: Literal["maintenance"]
    task: str
    # other = 위 값으로 나타낼 수 없는 주기 (예: "At least every six months", "At least every 10 years").
    #   가까운 값(monthly 등)으로 바꿔 넣으면 주기가 틀리게 안내되므로, 원문을 interval_note에 그대로 적는다.
    interval: Literal["daily", "weekly", "monthly", "quarterly", "yearly", "operating_hours", "other"]
    interval_hours: int | None = None        # interval = operating_hours일 때만. 범위면 아래쪽 값 (1000-1500 → 1000)
    interval_hours_max: int | None = None    # 원문이 범위일 때 위쪽 값 (1000-1500 → 1500). 한 값이면 비움
    first_interval_hours: int | None = None  # 첫 주기가 다른 경우 (예: 첫 500 h)
    interval_note: str | None = None         # 원문 그대로의 보충 (예: "or 12 months", "1000-1500 hours")

    criteria: str | None = None              # 판정 기준 (예: "Replace at 3 mm wear")
    details: list[str] = []                  # 윤활유 종류·양 등
    lockout_required: bool

    @model_validator(mode="after")
    def _check_interval(self):
        if not self.kb_id.startswith("MT-"):
            raise ValueError("점검 KB의 kb_id는 MT-로 시작해야 함")
        if (self.interval == "operating_hours") != (self.interval_hours is not None):
            raise ValueError("interval_hours는 interval이 operating_hours일 때만, 그리고 반드시 적어야 함")
        # 범위는 "아래쪽 값 + 위쪽 값"으로 나눠 적는다. 한 칸에 대표값만 두면 LLM이 범위를 한 값으로 단정할 수 있음
        if self.interval_hours_max is not None:
            if self.interval_hours is None or self.interval_hours_max <= self.interval_hours:
                raise ValueError("interval_hours_max는 interval_hours와 함께 적고, interval_hours보다 커야 함")
        if self.interval == "other" and not self.interval_note:
            raise ValueError("interval이 other이면 원문 주기를 interval_note에 적어야 함")

        return self


KbEntry = Annotated[Union[TroubleshootingKB, MaintenanceKB], Field(discriminator="type")]


# ------------------------------------------------------------------------------
# 응답 JSON 스키마 (WBS 2.1.4, 조윤빈). 설명은 응답 JSON 스키마 문서 v0.5
#   - AnswerResponse : RagPipeline.answer가 돌려주는 응답 1건
#                      (화면·로그·대화 기록·채점이 읽음)
#   - LLMAnswer      : generate가 llm.chat(json_schema=LLMAnswer)로 받는 부분
# 코드값(status, input_type 등)은 소문자로 쓴다.
# ------------------------------------------------------------------------------
SCHEMA_VERSION = "0.5"

# 원인 id = {kb_id}#c{순번}. KbCause.cause_id와 같은 규칙
# 예: TS-HYTROL138ACC-T01-04#c1
CAUSE_ID_PATTERN = KB_ID_PATTERN.removesuffix("$") + r"#c\d+$"

Status = Literal["answered", "not_found", "nameplate", "retake", "error"]
InputType = Literal["text", "nameplate_photo", "damage_photo", "other_photo", "voice"]
QuestionType = Literal["troubleshooting", "maintenance", "procedure", "out_of_scope"]
# kb = KB 항목, text · table · figure = 매뉴얼 청크 (Section.type과 같은 값)
ChunkType = Literal["kb", "text", "table", "figure"]


class StrictModel(BaseModel):
    """정의하지 않은 키가 들어오면 검증에 실패한다."""

    model_config = ConfigDict(extra="forbid")


class Cause(StrictModel):
    """원인 1개와 조치. KB 원인·조치(영어)를 한국어로 옮긴 문장. id·조치 개수·순서는 KB와 같아야 한다."""

    id: str = Field(pattern=CAUSE_ID_PATTERN)
    cause: str  # KbCause.cause
    actions: list[str]  # KbCause.actions (점검할 순서)


class ActionStep(StrictModel):
    """점검·절차의 한 단계 (maintenance · procedure 답변)."""

    text: str
    ref_id: str  # sources[].ref_id 중 하나


class LLMSource(StrictModel):
    ref_id: str  # 검색 결과 id (kb_id 또는 chunk_id)
    quote: str  # 매뉴얼 원문 그대로


class LLMAnswer(StrictModel):
    """generate()가 LLM에게 받는 부분. strict 모드를 위해 기본값을 두지 않는다."""

    not_found: bool
    summary: str
    causes: list[Cause]  # 원인 id는 KB 그대로, 원인·조치는 한국어로 옮김
    actions: list[ActionStep]
    sources: list[LLMSource]


class Source(LLMSource):
    """근거 1건. ref_id · quote는 LLM이, 나머지는 검색 메타데이터로 코드가 채운다."""

    doc_id: str
    doc_title: str
    page: int
    chunk_type: ChunkType
    image_path: str | None = None


class SafetyNote(StrictModel):
    rule_id: str
    text: str
    doc_title: str | None = None
    page: int | None = None


class Figure(StrictModel):
    label: str
    caption: str
    doc_id: str
    page: int
    image_path: str


class Equipment(StrictModel):
    model: str
    doc_ids: list[str]
    source: Literal["nameplate", "question"]


class NameplateResult(StrictModel):
    raw_text: str | None  # 판독한 모델명 글자 그대로 (예: "138 ACC")
    serial: str | None
    maker: str | None
    matched_model: str | None  # models.csv에서 찾은 모델 (match_model이 채움)
    match: Literal["exact", "near", "none"]
    confidence: float = Field(ge=0, le=1)  # 판독 확신도


class DamageResult(StrictModel):
    label: Literal["hole", "tear", "joint", "edge_damage", "other"]
    symptom_ko: str
    confidence: float = Field(ge=0, le=1)  # 기록용 (재촬영은 증상 문장이 없을 때)


class ImageResult(StrictModel):
    kind: Literal["nameplate", "belt_damage", "other"]
    confidence: float = Field(ge=0, le=1)  # 사진 종류 판별 확신도
    nameplate: NameplateResult | None = None
    damage: DamageResult | None = None


class TraceHit(StrictModel):
    id: str
    score: float
    doc_id: str
    page: int | None = None

    @classmethod
    def from_hit(cls, hit: Hit) -> TraceHit:
        """검색 결과에서 로그·채점에 필요한 값만 남긴다."""
        return cls(id=hit.id, score=hit.score, doc_id=hit.doc_id, page=hit.page)


class TraceError(StrictModel):
    stage: str  # understand, vision, search, generate, verify, safety, db
    code: str  # 예: llm_timeout, db_disconnected
    message: str


class Trace(StrictModel):
    queries_en: list[str] = []
    hits: list[TraceHit] = []  # 리랭킹 후 상위 5개
    top_score: float | None = None
    threshold: float | None = None
    # 키: understand, vision, search, generate, verify, safety, total
    latency_ms: dict[str, int] = {}
    schema_retries: int = 0
    generate_fail: str | None = None  # 생성 단계 확인되지 않음 이유: llm_not_found · not_korean · bad_format (진단용)
    removed_cause_ids: list[str] = []
    replaced_quote_refs: list[str] = []
    error: TraceError | None = None


class AnswerResponse(StrictModel):
    """RagPipeline.answer가 돌려주는 채팅 응답 1건."""

    schema_version: str = SCHEMA_VERSION
    status: Status
    message: str | None = None
    input_type: InputType
    question_type: QuestionType | None = None
    equipment: Equipment | None = None
    filter_released: bool = False
    summary: str | None = None
    safety: list[SafetyNote] = []
    causes: list[Cause] = []
    actions: list[ActionStep] = []
    sources: list[Source] = []
    figures: list[Figure] = []
    kb_ids: list[str] = []
    image: ImageResult | None = None
    trace: Trace = Field(default_factory=Trace)

    @model_validator(mode="after")
    def check_status_fields(self) -> AnswerResponse:
        """status마다 꼭 있어야 하는 필드를 확인한다 (응답 스키마 문서 5장)."""
        if self.status == "answered":
            if not self.summary or not self.sources:
                raise ValueError("answered에는 summary와 sources가 필요합니다")
            if not (self.causes or self.actions):
                raise ValueError("answered에는 causes 또는 actions가 필요합니다")
            if self.question_type == "troubleshooting" and self.actions:
                raise ValueError("troubleshooting 답변의 actions는 비워야 합니다")
        elif not self.message:
            raise ValueError(f"{self.status}에는 message가 필요합니다")
        return self
