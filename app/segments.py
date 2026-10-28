"""Turning parsed text into Segment objects."""

import math
import re

from app.schema import Segment

BULLET = re.compile(r"^\s*(?:[-*•▪◦‣–]|\d{1,3}[.)]|[a-zA-Z][.)]|\(\d{1,3}\)|step\s+\d+[:.)-]?)\s+", re.I)
CONTINUE_END = re.compile(r"(?:[,;/&(\-–]|\b(?:and|or|the|a|to|of|then|with|for|in|on))$", re.I)


def make_id(source_index: int, seq: int) -> str:
    return f"S{source_index}-{seq:04d}"


def split_note_lines(text: str) -> list[str]:
    """Split notes into lines/bullets, merging hard-wrapped lines."""
    items: list[str] = []
    current: str | None = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            if current:
                items.append(current)
            current = None
            continue
        is_bullet = bool(BULLET.match(line))
        cleaned = BULLET.sub("", line).strip() if is_bullet else line
        if current is not None and not is_bullet and (line[0].islower() or CONTINUE_END.search(current)):
            current = f"{current} {cleaned}"
            continue
        if current:
            items.append(current)
        current = cleaned
    if current:
        items.append(current)
    return [i for i in (re.sub(r"\s+", " ", x).strip() for x in items) if i]


def role_from_question(text: str) -> str:
    return "interviewer" if text.rstrip().endswith("?") else "staff"


def note_segments(text: str, source_index: int) -> list[Segment]:
    return [
        Segment(id=make_id(source_index, i), source_index=source_index, kind="note_line",
                speaker_role="staff", text=line[:20000], confidence=1.0)
        for i, line in enumerate(split_note_lines(text), start=1)
    ]


def turn_segments(turns: list[dict], source_index: int) -> tuple[list[Segment], dict[str, str]]:
    segs, speakers = [], {}
    for i, t in enumerate(turns, start=1):
        sid = make_id(source_index, i)
        role = "staff" if t.get("speaker") else role_from_question(t["text"])
        segs.append(Segment(id=sid, source_index=source_index, kind="transcript_turn", speaker_role=role,
                            text=t["text"][:20000], start_s=t.get("start"), end_s=t.get("end"),
                            confidence=1.0))
        if t.get("speaker"):
            speakers[sid] = t["speaker"]
    return segs, speakers


def asr_segments(items: list[dict], source_index: int, seq_start: int, offset_s: float,
                 kind: str) -> list[Segment]:
    out: list[Segment] = []
    seq = seq_start
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        conf = None
        if it.get("avg_logprob") is not None:
            conf = max(0.0, min(1.0, math.exp(float(it["avg_logprob"]))))
        role = "narrator" if kind == "narration" else role_from_question(text)
        out.append(Segment(
            id=make_id(source_index, seq), source_index=source_index, kind=kind, speaker_role=role,
            text=text, start_s=round(float(it.get("start", 0)) + offset_s, 2),
            end_s=round(float(it.get("end", 0)) + offset_s, 2), confidence=conf,
        ))
        seq += 1
    return out
