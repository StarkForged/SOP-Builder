"""Structural checks (trigger repair) and evidence checks (adjust item status)."""

from rapidfuzz import fuzz

from app.schema import SOP, Question, Segment

RANK = {"supported": 0, "inferred": 1, "needs_confirmation": 2}


def iter_items(sop: SOP):
    """Yield (item, label, list of text fields) for every evidence-bearing item."""
    for p in sop.prerequisites:
        yield p, f"Prerequisite {p.id}", [p.text]
    for s in sop.steps:
        yield s, f"Step {s.number}", [s.action, s.condition or "", s.expected_result or "", *s.ui_elements]
    for w in sop.warnings:
        yield w, f"Warning {w.id}", [w.text]


def downgrade(item, status: str) -> bool:
    if RANK[status] > RANK[item.status]:
        item.status = status
        return True
    return False


def new_question(question: str, reason: str, category: str, severity: str, related: list[str],
                 origin: str) -> Question:
    return Question(id="q-0", question=question, reason=reason, category=category, severity=severity,
                    related_item_ids=related, origin=origin)


def structural_errors(sop: SOP, segments: dict[str, Segment]) -> list[str]:
    errors = []
    numbers = [s.number for s in sop.steps]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"Step numbers must be 1..{len(numbers)} in order; got {numbers}.")
    ids = [x.id for x in [*sop.prerequisites, *sop.steps, *sop.warnings, *sop.questions, *sop.checklist]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"Duplicate ids: {dupes}.")
    for item, label, _ in iter_items(sop):
        for ref in item.source_refs:
            if ref.segment_id not in segments:
                errors.append(f"{label} ({item.id}) cites unknown segment_id {ref.segment_id}; "
                              "use only ids from <sources>.")
    return errors


def apply_evidence_rules(sop: SOP, segments: dict[str, Segment]) -> tuple[list[dict], list[Question]]:
    flags: list[dict] = []
    questions: list[Question] = []
    for item, label, _ in iter_items(sop):
        refs = item.source_refs
        if not refs:
            if item.status == "supported" and downgrade(item, "needs_confirmation"):
                flags.append({"item_id": item.id, "flag": "supported_without_refs"})
            continue
        for ref in refs:
            seg = segments[ref.segment_id]
            if fuzz.partial_ratio(ref.quote.lower(), seg.text.lower()) < 90:
                downgrade(item, "needs_confirmation")
                flags.append({"item_id": item.id, "flag": "quote_mismatch", "segment_id": ref.segment_id})
        roles = {segments[r.segment_id].speaker_role for r in refs}
        kinds = {segments[r.segment_id].kind for r in refs}
        if roles == {"interviewer"}:
            downgrade(item, "needs_confirmation")
            flags.append({"item_id": item.id, "flag": "interviewer_only"})
        if kinds == {"screen_text"}:
            downgrade(item, "inferred")
            flags.append({"item_id": item.id, "flag": "visual_only"})
            questions.append(new_question(
                f"{label} is based only on what was visible on screen. Can staff confirm this is the "
                "correct action and when it applies?",
                "Only screen text supports this item; no narration, notes or interview confirm it.",
                "visual_only_evidence", "mandatory", [item.id], "rule"))
    return flags, questions
