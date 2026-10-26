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
    role: str  # "engineer" | "admin"

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


class Cause(BaseModel):
    id: str
    text: str


class Source(BaseModel):
    doc_id: str
    page: int
    quote: str = ""


class AnswerResponse(BaseModel):
    """LLM 응답 JSON 형식. validator.validate_schema가 이 모델로 검사."""
    summary: str = ""
    safety: list[str] = []
    causes: list[Cause] = []
    actions: list[str] = []
    sources: list[Source] = []
    figures: list[str] = []
    follow_ups: list[str] = []
    not_found: bool = False


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
    page: int = Field(ge=1)  # 원본 PDF(sources.csv의 original_file) 쪽. 잘라 낸 파일 쪽·인쇄 쪽 아님
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
        # ["*", "138ACC"]처럼 섞어 쓰면 "모든 모델"인지 "일부 모델"인지 애매해지므로 막는다.
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
    interval: Literal["daily", "weekly", "monthly", "quarterly", "yearly", "operating_hours"]
    interval_hours: int | None = None        # interval = operating_hours일 때만
    first_interval_hours: int | None = None  # 첫 주기가 다른 경우 (예: 첫 500 h)
    interval_note: str | None = None         # 원문 그대로의 보충 (예: "or 12 months")
    criteria: str | None = None              # 판정 기준 (예: "Replace at 3 mm wear")
    details: list[str] = []                  # 윤활유 종류·양 등
    lockout_required: bool

    @model_validator(mode="after")
    def _check_interval(self):
        if not self.kb_id.startswith("MT-"):
            raise ValueError("점검 KB의 kb_id는 MT-로 시작해야 함")
        if (self.interval == "operating_hours") != (self.interval_hours is not None):
            raise ValueError("interval_hours는 interval이 operating_hours일 때만, 그리고 반드시 적어야 함")
        return self


KbEntry = Annotated[Union[TroubleshootingKB, MaintenanceKB], Field(discriminator="type")]
