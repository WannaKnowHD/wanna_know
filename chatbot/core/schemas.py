"""공통 데이터 클래스. 여러 파일이 같이 쓰는 타입을 한곳에 모아 순환 import를 막음."""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


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
