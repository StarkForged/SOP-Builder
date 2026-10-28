"""Pydantic models: segments, SOP content, API requests."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ItemStatus = Literal["supported", "inferred", "needs_confirmation"]
SegmentKind = Literal["note_line", "transcript_turn", "narration", "screen_text", "reviewer_answer"]
SpeakerRole = Literal["staff", "interviewer", "narrator", "system", "reviewer"]
QuestionCategory = Literal[
    "approval_rule", "threshold", "eligibility", "permission_access", "failure_handling",
    "verification", "ambiguous_term", "missing_document", "conflict", "visual_only_evidence",
    "invented_detail", "other",
]
MANDATORY_CATEGORIES = {
    "approval_rule", "threshold", "eligibility", "permission_access", "failure_handling",
    "conflict", "visual_only_evidence", "invented_detail",
}
SEG_ID = r"^S\d+-\d{4}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Segment(Strict):
    id: str = Field(pattern=SEG_ID)
    source_index: int = Field(ge=0)
    kind: SegmentKind
    speaker_role: SpeakerRole
    text: str = Field(max_length=20000)
    start_s: float | None = None
    end_s: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class SourceRef(Strict):
    segment_id: str = Field(pattern=SEG_ID)
    quote: str = Field(min_length=3, max_length=300)


class Prerequisite(Strict):
    id: str = Field(pattern=r"^pre-\d+$")
    text: str
    source_refs: list[SourceRef]
    status: ItemStatus


class Step(Strict):
    id: str = Field(pattern=r"^step-\d+$")
    number: int = Field(ge=1)
    kind: Literal["action", "decision", "verification"]
    actor: str | None
    action: str
    condition: str | None
    expected_result: str | None
    ui_elements: list[str]
    source_refs: list[SourceRef]
    status: ItemStatus


class Warning(Strict):
    id: str = Field(pattern=r"^warn-\d+$")
    text: str
    severity: Literal["caution", "critical"]
    source_refs: list[SourceRef]
    status: ItemStatus


class Question(Strict):
    id: str = Field(pattern=r"^q-\d+$")
    question: str
    reason: str
    category: QuestionCategory
    severity: Literal["mandatory", "optional"]
    related_item_ids: list[str]
    origin: Literal["llm", "rule", "detector"]


class ChecklistItem(Strict):
    id: str = Field(pattern=r"^chk-\d+$")
    text: str


class SOP(Strict):
    title: str
    purpose: str
    prerequisites: list[Prerequisite]
    steps: list[Step] = Field(min_length=1)
    warnings: list[Warning]
    questions: list[Question]
    checklist: list[ChecklistItem]


class Answer(Strict):
    question_id: str = Field(max_length=50)
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=5000)


class GenerateRequest(Strict):
    title_hint: str = Field(default="", max_length=200)
    segments: list[Segment] = Field(max_length=5000)
    answers: list[Answer] = Field(default_factory=list, max_length=500)


class ExportRequest(Strict):
    sop: SOP
    answers: list[Answer] = Field(default_factory=list)
    reviewer_name: str = Field(default="", max_length=120)
    format: Literal["docx", "md"]
    segments: list[Segment] = Field(default_factory=list)
