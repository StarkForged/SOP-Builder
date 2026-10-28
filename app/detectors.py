"""Deterministic detectors for specific facts in the SOP that are absent from the sources."""

import re

from rapidfuzz import fuzz

from app.schema import SOP, Question, Segment
from app.validators import downgrade, iter_items, new_question

WORD_NUMS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15",
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "hundred": "100",
}
NUM = r"\d+(?:[.,]\d+)*"
MONEY = re.compile(rf"(?i)(?:₹|\$|€|\brs\.?|\binr)\s?({NUM})|({NUM})\s?(?:inr|rupees|dollars|euros|usd)\b")
PERCENT = re.compile(rf"(?i)({NUM})\s?(?:%|percent\b|per cent\b)")
DURATION = re.compile(
    rf"(?i)({NUM})\s?(?:-\s?{NUM}\s?)?(seconds?|secs?|minutes?|mins?|hours?|hrs?|"
    r"(?:business |working )?days?|weeks?|months?|years?)\b"
)
NUMBER = re.compile(NUM)
QUOTED = re.compile(r"\"([^\"]{2,80})\"|“([^”]{2,80})”|'([^']{3,80})'|‘([^’]{2,80})’")
EMAIL_URL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://\S+|www\.\S+")
UNIT_BASE = {"sec": "second", "min": "minute", "hr": "hour", "hou": "hour", "day": "day", "bus": "day",
             "wor": "day", "wee": "week", "mon": "month", "yea": "year"}


def norm_num(s: str) -> str:
    s = s.replace(",", "")
    return s[:-2] if s.endswith(".0") else s


def unit_base(unit: str) -> str:
    return UNIT_BASE.get(unit.lower()[:3], unit.lower())


def normalize_corpus(text: str) -> str:
    text = text.lower()
    return re.sub(r"\b(" + "|".join(WORD_NUMS) + r")\b", lambda m: WORD_NUMS[m.group(1)], text)


class Corpus:
    def __init__(self, segments: list[Segment]):
        self.texts = [normalize_corpus(s.text) for s in segments]
        self.full = "\n".join(self.texts)
        self.numbers = {norm_num(n) for n in NUMBER.findall(self.full)}
        self.durations = {(norm_num(m.group(1)), unit_base(m.group(2))) for m in DURATION.finditer(self.full)}

    def has_phrase(self, phrase: str) -> bool:
        p = normalize_corpus(phrase).strip()
        if not p:
            return True
        if p in self.full:
            return True
        return any(fuzz.partial_ratio(p, t) >= 90 for t in self.texts if len(t) >= len(p) * 0.5)


def find_hits(text: str, corpus: Corpus, ui_elements: list[str] | None = None) -> list[dict]:
    hits: list[dict] = []
    seen_spans: list[tuple[int, int]] = []

    def add(kind: str, value: str, span: tuple[int, int]):
        if any(a <= span[0] and span[1] <= b for a, b in seen_spans):
            return
        seen_spans.append(span)
        hits.append({"type": kind, "value": value})

    for m in EMAIL_URL.finditer(text):
        if m.group(0).lower().rstrip(".,)") not in corpus.full:
            add("email_url", m.group(0), m.span())
        seen_spans.append(m.span())
    for m in MONEY.finditer(text):
        if norm_num(m.group(1) or m.group(2)) not in corpus.numbers:
            add("money", m.group(0).strip(), m.span())
        seen_spans.append(m.span())
    for m in PERCENT.finditer(text):
        if norm_num(m.group(1)) not in corpus.numbers:
            add("percent", m.group(0).strip(), m.span())
        seen_spans.append(m.span())
    for m in DURATION.finditer(text):
        if (norm_num(m.group(1)), unit_base(m.group(2))) not in corpus.durations:
            add("duration", m.group(0).strip(), m.span())
        seen_spans.append(m.span())
    for m in NUMBER.finditer(text):
        if norm_num(m.group(0)) not in corpus.numbers:
            add("number", m.group(0), m.span())
    for m in QUOTED.finditer(text):
        q = next(g for g in m.groups() if g)
        if not corpus.has_phrase(q):
            add("quoted_text", q, m.span())
    for ui in ui_elements or []:
        if ui.strip() and not corpus.has_phrase(ui):
            hits.append({"type": "ui_element", "value": ui})
    return hits


def run_detectors(sop: SOP, segments: list[Segment]) -> tuple[list[dict], list[Question]]:
    corpus = Corpus(segments)
    all_hits: list[dict] = []
    questions: list[Question] = []
    for item, label, texts in iter_items(sop):
        ui = list(getattr(item, "ui_elements", []) or [])
        body = texts[: len(texts) - len(ui)] if ui else texts
        hits = []
        for t in body:
            hits.extend(find_hits(t, corpus))
        hits.extend(find_hits("", corpus, ui))
        if not hits:
            continue
        downgrade(item, "needs_confirmation")
        for h in hits:
            all_hits.append({"item_id": item.id, **h})
        values = ", ".join(f'"{h["value"]}"' for h in hits)
        questions.append(new_question(
            f"{label} states {values}, which does not appear in any source. What is the correct value?",
            "Specific detail not found in the provided sources (possible invented detail).",
            "invented_detail", "mandatory", [item.id], "detector"))
    return all_hits, questions
