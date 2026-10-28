"""DOCX and Markdown export."""

import datetime as dt
import io
import re

from docx import Document

from app.redact import redact
from app.schema import ExportRequest

DRAFT_TITLE = "DRAFT – NOT VERIFIED"
STATUS_LABEL = {"supported": "supported", "inferred": "inferred", "needs_confirmation": "needs confirmation"}


def _answers(req: ExportRequest) -> dict[str, str]:
    return {a.question_id: a.answer.strip() for a in req.answers if a.answer.strip()}


def open_mandatory(req: ExportRequest):
    ans = _answers(req)
    return [q for q in req.sop.questions if q.severity == "mandatory" and q.id not in ans]


def _fmt_t(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def build_model(req: ExportRequest) -> dict:
    """Collect everything both formats need (all text redacted)."""
    sop, ans = req.sop, _answers(req)
    open_q = open_mandatory(req)
    seg_map = {s.id: s for s in req.segments}
    refs = []
    for item in [*sop.prerequisites, *sop.steps, *sop.warnings]:
        for r in item.source_refs:
            seg = seg_map.get(r.segment_id)
            refs.append((item.id, r.segment_id, r.quote, _fmt_t(seg.start_s) if seg else ""))
    reviewer = req.reviewer_name.strip() or "unnamed reviewer"
    banner = DRAFT_TITLE if open_q else f"Reviewed by {reviewer} on {dt.date.today().isoformat()}"
    steps = []
    for s in sop.steps:
        line = s.action
        if s.kind == "decision" and s.condition:
            line = f"Decision – if {s.condition}: {s.action}"
        elif s.condition:
            line = f"{s.action} (when: {s.condition})"
        extra = []
        if s.actor:
            extra.append(f"Actor: {s.actor}")
        if s.expected_result:
            extra.append(f"Expected result: {s.expected_result}")
        if s.ui_elements:
            extra.append("UI: " + ", ".join(s.ui_elements))
        extra.append(f"Status: {STATUS_LABEL[s.status]}")
        steps.append((s.number, line, extra))
    r = redact
    return {
        "draft": bool(open_q),
        "banner": banner,
        "title": r(sop.title),
        "purpose": r(sop.purpose),
        "prerequisites": [r(f"{p.text} [{STATUS_LABEL[p.status]}]") for p in sop.prerequisites],
        "steps": [(n, r(line), [r(e) for e in extra]) for n, line, extra in steps],
        "warnings": [r(f"{w.severity.upper()}: {w.text} [{STATUS_LABEL[w.status]}]") for w in sop.warnings],
        "checklist": [r(c.text) for c in sop.checklist],
        "answered": [(r(q.question), r(ans[q.id])) for q in sop.questions if q.id in ans],
        "open": [r(f"[{q.severity}] {q.question}") for q in sop.questions if q.id not in ans],
        "refs": [(i, sid, r(quote), t) for i, sid, quote, t in refs],
    }


def to_markdown(req: ExportRequest) -> str:
    m = build_model(req)
    out = [f"# {m['banner']}\n" if m["draft"] else "", f"# {m['title']}\n"]
    if not m["draft"]:
        out.append(f"_{m['banner']}_\n")
    out += ["## Purpose\n", m["purpose"] + "\n", "## Prerequisites\n"]
    out += [f"- {p}" for p in m["prerequisites"]] or ["- None listed"]
    out.append("\n## Steps\n")
    for n, line, extra in m["steps"]:
        out.append(f"{n}. {line}")
        out += [f"   - {e}" for e in extra]
    out.append("\n## Warnings\n")
    out += [f"- {w}" for w in m["warnings"]] or ["- None listed"]
    out.append("\n## Checklist\n")
    out += [f"- ☐ {c}" for c in m["checklist"]]
    out.append("\n## Answered questions\n")
    out += [f"- **{q}** — {a}" for q, a in m["answered"]] or ["- None"]
    if m["draft"] or m["open"]:
        out.append("\n## Appendix: Open questions\n")
        out += [f"- {q}" for q in m["open"]] or ["- None"]
    out.append("\n## Appendix: Sources\n")
    out += [f"- {i} ← {sid}{' @ ' + t if t else ''}: “{q}”" for i, sid, q, t in m["refs"]] or ["- None"]
    return "\n".join(x for x in out if x != "") + "\n"


def to_docx(req: ExportRequest) -> bytes:
    m = build_model(req)
    doc = Document()
    if m["draft"]:
        doc.add_heading(m["banner"], level=0)
        doc.add_heading(m["title"], level=1)
    else:
        doc.add_heading(m["title"], level=0)
        doc.add_paragraph(m["banner"])
    doc.add_heading("Purpose", level=1)
    doc.add_paragraph(m["purpose"])
    doc.add_heading("Prerequisites", level=1)
    for p in m["prerequisites"] or ["None listed"]:
        doc.add_paragraph(p, style="List Bullet")
    doc.add_heading("Steps", level=1)
    for n, line, extra in m["steps"]:
        doc.add_paragraph(f"{n}. {line}")
        for e in extra:
            doc.add_paragraph(e, style="List Bullet 2")
    doc.add_heading("Warnings", level=1)
    for w in m["warnings"] or ["None listed"]:
        doc.add_paragraph(w, style="List Bullet")
    doc.add_heading("Checklist", level=1)
    for c in m["checklist"]:
        doc.add_paragraph(f"☐ {c}")
    doc.add_heading("Answered questions", level=1)
    for q, a in m["answered"] or [("None", "")]:
        doc.add_paragraph(f"{q} — {a}" if a else q, style="List Bullet")
    if m["draft"] or m["open"]:
        doc.add_heading("Appendix: Open questions", level=1)
        for q in m["open"] or ["None"]:
            doc.add_paragraph(q, style="List Bullet")
    doc.add_heading("Appendix: Sources", level=1)
    for i, sid, q, t in m["refs"] or [("", "None", "", "")]:
        doc.add_paragraph(f"{i} ← {sid}{' @ ' + t if t else ''}: “{q}”" if i else sid, style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def filename(req: ExportRequest, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", req.sop.title).strip("-").lower()[:60] or "sop"
    return f"{slug}.{ext}"
