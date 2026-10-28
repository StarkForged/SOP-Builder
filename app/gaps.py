"""Deterministic gap rules and question merging/deduplication."""

import re

from rapidfuzz import fuzz

from app.detectors import DURATION, MONEY, PERCENT, norm_num, unit_base
from app.schema import MANDATORY_CATEGORIES, SOP, Question, Segment
from app.validators import iter_items, new_question

VAGUE = ["soon", "asap", "as required", "sometimes", "usually", "appropriate", "appropriately", "etc",
         "quickly", "when necessary", "as needed", "if needed", "regularly", "promptly", "shortly"]
VAGUE_RE = re.compile(r"(?i)\b(" + "|".join(re.escape(v) for v in VAGUE) + r")\b")
APPROVAL = re.compile(r"(?i)\bapprov\w*|\bsign[- ]off\b|\bauthori[sz]\w*")
CONDITION_WORDS = re.compile(
    r"(?i)\b(if|when|above|over|below|under|more than|less than|exceed\w*|unless|only|greater|"
    r"at least|up to|within|before|after)\b|[<>≥≤]|\d"
)
FAILURE = re.compile(
    r"(?i)\b(fail\w*|error\w*|reject\w*|declin\w*|exception|escalat\w*|retry|otherwise|unable|cannot|"
    r"can't|invalid|wrong|incorrect|problems?|issues? with|not (?:work|match|approved|available))\b"
)
SYSTEM = re.compile(
    r"(?i)\b(system|dashboard|portal|log ?in|sign in|software|application|crm|erp|admin panel|"
    r"account|website|tool)\b"
)
ACCESS = re.compile(r"(?i)\b(access|log ?in|credential\w*|permission\w*|account|role|rights|password)\b")
DOCUMENT = re.compile(
    r"(?i)\b((?:[A-Za-z][\w-]*\s){0,3}(?:policy|form|template|manual|guideline|guidelines|handbook|"
    r"sheet|document))\b"
)
STOP = set("""a an the and or to of in on for with from by at as is are be was were it this that these those
then than if when need needs needed must should will can may any all each per our your their its into out
over above below under more less what which who whom has have had not no yes do does did get got""".split())


def _stem(w: str) -> str:
    w = w.lower().strip("'’s")
    return w[:-1] if w.endswith("s") and len(w) > 4 else w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[A-Za-z']{4,}", text) if w.lower() not in STOP}


def _quantities(text: str) -> list[tuple[str, str]]:
    out = [("money", norm_num(m.group(1) or m.group(2))) for m in MONEY.finditer(text)]
    out += [("percent", norm_num(m.group(1))) for m in PERCENT.finditer(text)]
    out += [(f"duration:{unit_base(m.group(2))}", norm_num(m.group(1))) for m in DURATION.finditer(text)]
    return out


def conflict_questions(segments: list[Segment]) -> list[Question]:
    qs: list[Question] = []
    seen: set[tuple] = set()
    data = [(s, _quantities(s.text), _words(s.text)) for s in segments if s.speaker_role != "interviewer"]
    for i, (a, qa, wa) in enumerate(data):
        for b, qb, wb in data[i + 1:]:
            if a.speaker_role == "reviewer" or b.speaker_role == "reviewer":
                continue
            shared = wa & wb
            if not shared:
                continue
            for cat, va in qa:
                for cat_b, vb in qb:
                    key = (cat, frozenset((va, vb)))
                    if cat == cat_b and va != vb and key not in seen:
                        seen.add(key)
                        qs.append(new_question(
                            f'Sources disagree: {a.id} says "{a.text[:120]}" but {b.id} says "{b.text[:120]}". '
                            "Which value is correct?",
                            f"Two sources give different {cat.split(':')[0]} values ({va} vs {vb}).",
                            "conflict", "mandatory", [], "rule"))
    return qs


def gap_questions(sop: SOP, segments: list[Segment]) -> list[Question]:
    qs: list[Question] = []
    all_text = []
    for item, label, texts in iter_items(sop):
        text = " ".join(t for t in texts if t)
        all_text.append(text)
        for term in sorted({m.group(1).lower() for m in VAGUE_RE.finditer(text)}):
            qs.append(new_question(
                f'{label} uses the vague term "{term}". What exactly does it mean here (time limit, amount '
                "or criteria)?", "Vague wording cannot be followed consistently.",
                "ambiguous_term", "optional", [item.id], "rule"))
        cond = getattr(item, "condition", None) or ""
        if APPROVAL.search(text) and not CONDITION_WORDS.search(text + " " + cond):
            qs.append(new_question(
                f"{label} mentions approval. Who must approve, and in which cases is approval required?",
                "Approval is mentioned without a rule for when it applies.",
                "approval_rule", "mandatory", [item.id], "rule"))
        if getattr(item, "kind", None) == "decision" and (not cond.strip() or VAGUE_RE.search(cond)):
            qs.append(new_question(
                f"{label} is a decision. What exact criteria decide which path to take?",
                "Decision step without clear criteria.", "eligibility", "mandatory", [item.id], "rule"))
        for m in DOCUMENT.finditer(text):
            phrase = m.group(1).strip()
            if re.search(r"(?i)\b(this|the sop)\b", phrase) and len(phrase.split()) <= 2:
                continue
            qs.append(new_question(
                f'{label} refers to "{phrase}", which was not provided. Please share or describe it.',
                "Referenced document is not included in the sources.",
                "missing_document", "optional", [item.id], "rule"))
    joined = " ".join(all_text)
    if not FAILURE.search(joined):
        qs.append(new_question(
            "What should staff do if a step fails or the result is not as expected (errors, rejections)?",
            "The SOP has no failure or error handling.", "failure_handling", "mandatory", [], "rule"))
    if not any(s.kind == "verification" or (s.expected_result or "").strip() for s in sop.steps):
        qs.append(new_question(
            "How does staff verify that the procedure was completed correctly?",
            "No verification step or expected result.", "verification", "optional", [], "rule"))
    prereq_text = " ".join(p.text for p in sop.prerequisites)
    if SYSTEM.search(joined) and not ACCESS.search(prereq_text):
        qs.append(new_question(
            "Which system access, login or permissions does staff need before starting?",
            "A system is used but no access prerequisite is listed.",
            "permission_access", "mandatory", [], "rule"))
    qs.extend(conflict_questions(segments))
    return qs


def merge_questions(groups: list[list[Question]], answered: list[str]) -> list[Question]:
    merged: list[Question] = []
    for group in groups:
        for q in group:
            if any(fuzz.token_set_ratio(q.question, a) >= 80 for a in answered):
                continue
            dup = next((m for m in merged if fuzz.token_set_ratio(q.question, m.question) >= 80), None)
            if dup:
                dup.related_item_ids = list(dict.fromkeys(dup.related_item_ids + q.related_item_ids))
                if q.severity == "mandatory":
                    dup.severity = "mandatory"
                continue
            merged.append(q.model_copy(deep=True))
    for i, q in enumerate(merged, start=1):
        q.id = f"q-{i}"
        if q.category in MANDATORY_CATEGORIES:
            q.severity = "mandatory"
    return merged
