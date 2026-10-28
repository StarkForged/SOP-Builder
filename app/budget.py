"""Token budgeting for the Groq free tier: estimate tokens, clean sources and condense them to fit."""

import html
import re

from app.schema import Segment

ACTION = re.compile(
    r"(?i)\b(open|click|enter|type|select|choose|press|submit|send|check|verify|confirm|approve|review|upload|"
    r"download|log|sign|fill|create|add|update|save|call|email|print|scan|record|close|mark|assign|attach|"
    r"navigate|go|search|find|issue|refund|process|complete|contact|notify|request|pay|transfer|set|start|"
    r"wait|ensure|run|calculate|compare|raise|escalate|forward|reply|schedule|ship|pack|cancel|reject|"
    r"validate|generate|export|delete|remove|edit|change|inspect|test|collect|deliver|receive|count)\w*\b"
)
CONDITION = re.compile(r"(?i)\b(if|when|unless|only|before|after|above|below|over|under|more than|less than|"
                       r"within|until|otherwise|either|or else)\b")
WARNING = re.compile(r"(?i)\b(never|always|must|do not|don't|careful|warning|important|error|fail\w*|"
                     r"approv\w*|manager|permission|access|policy|form|escalat\w*)\b")
NUMBER = re.compile(r"\d|₹|\$|€|%")


def est_tokens(text: str) -> int:
    """Rough token estimate (chars / 4), rounded up."""
    return (len(text) + 3) // 4


def clean_segments(segments: list[Segment], max_chars: int) -> list[Segment]:
    """Collapse whitespace, drop empty and duplicate segments, cap each segment's text."""
    out, seen = [], set()
    for s in segments:
        text = " ".join(s.text.split())
        key = text.lower()
        if len(text) < 2 or key in seen:
            continue
        seen.add(key)
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        out.append(s.model_copy(update={"text": text}))
    return out


def relevance(seg: Segment) -> int:
    if seg.speaker_role == "reviewer":
        return 100  # authoritative answers always go first
    if seg.speaker_role == "interviewer":
        return 0
    t = seg.text
    return (3 * bool(ACTION.search(t)) + 2 * bool(CONDITION.search(t)) + 2 * bool(WARNING.search(t))
            + bool(NUMBER.search(t)))


def _t(seg: Segment) -> str:
    if seg.start_s is None:
        return ""
    end = seg.end_s if seg.end_s is not None else seg.start_s
    return f"{seg.start_s:.1f}-{end:.1f}"


def segment_row(s: Segment) -> str:
    attrs = " ".join(f'{k}="{html.escape(v, quote=True)}"' for k, v in
                     (("id", s.id), ("kind", s.kind), ("speaker", s.speaker_role), ("t", _t(s))))
    return f"<segment {attrs}>{html.escape(s.text, quote=True)}</segment>"


def build_bundle(segments: list[Segment]) -> str:
    return "<sources>\n" + "\n".join(segment_row(s) for s in segments) + "\n</sources>"


def condense(segments: list[Segment], token_budget: int) -> tuple[list[Segment], bool]:
    """Keep every segment if the bundle fits; otherwise keep the most relevant ones (original order)."""
    rows = [(i, s, est_tokens(segment_row(s)) + 1) for i, s in enumerate(segments)]
    overhead = est_tokens("<sources>\n\n</sources>")
    if overhead + sum(r[2] for r in rows) <= token_budget:
        return segments, False
    used, keep = overhead, []
    for i, s, cost in sorted(rows, key=lambda r: (-relevance(r[1]), r[0])):
        if relevance(s) == 0 and keep:
            continue
        if used + cost <= token_budget:
            keep.append(i)
            used += cost
    return [segments[i] for i in sorted(keep)], True
