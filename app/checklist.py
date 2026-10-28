"""Deterministic review checklist."""

from app.schema import ChecklistItem, Question

FIXED = [
    "All steps tested end-to-end",
    "Prerequisites and access confirmed",
    "Warnings reviewed by the process owner",
    "Approved by an authorised person",
]


def build_checklist(questions: list[Question]) -> list[ChecklistItem]:
    texts = [f"Confirm: {q.question}" for q in questions if q.severity == "mandatory"] + FIXED
    return [ChecklistItem(id=f"chk-{i}", text=t) for i, t in enumerate(texts, start=1)]
