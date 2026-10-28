"""SOP generation pipeline: redact -> bundle -> LLM -> validate/repair -> validators/detectors/gaps."""

import html
import json
import re

from pydantic import ValidationError

from app.ai import LLMClient, load_prompt
from app.checklist import build_checklist
from app.config import PROMPT_VERSION
from app.detectors import run_detectors
from app.gaps import gap_questions, merge_questions
from app.redact import redact
from app.schema import SOP, GenerateRequest, Segment
from app.segments import make_id
from app.validators import apply_evidence_rules, structural_errors

ACTION_VERBS = re.compile(
    r"(?i)\b(open|click|enter|type|select|choose|press|tap|hit|submit|send|check|verify|confirm|approve|"
    r"review|upload|download|log|login|sign|fill|create|add|update|save|call|email|print|scan|record|close|"
    r"mark|assign|attach|copy|paste|navigate|go|search|find|issue|refund|process|complete|contact|notify|"
    r"request|pay|transfer|move|set|start|stop|wait|ensure|run|install|calculate|compare|raise|escalate|"
    r"forward|reply|schedule|book|ship|pack|cancel|reject|validate|enter|fill|input|generate|export|import|"
    r"delete|remove|edit|change|inspect|test|measure|clean|lock|unlock|collect|deliver|receive|count|weigh)"
    r"(?:s|es|d|ed|ing|ted|ped|ting)?\b"
)


class InsufficientSource(Exception):
    pass


class GenerationFailed(Exception):
    pass


def answer_segments(req: GenerateRequest) -> list[Segment]:
    out = []
    for i, a in enumerate((a for a in req.answers if a.answer.strip()), start=1):
        out.append(Segment(id=make_id(0, i), source_index=0, kind="reviewer_answer", speaker_role="reviewer",
                           text=f"Q: {a.question} A: {a.answer}"[:20000], confidence=1.0))
    return out


def _t(seg: Segment) -> str:
    if seg.start_s is None:
        return ""
    end = seg.end_s if seg.end_s is not None else seg.start_s
    return f"{seg.start_s:.1f}-{end:.1f}"


def build_bundle(segments: list[Segment]) -> str:
    rows = []
    for s in segments:
        attrs = " ".join(f'{k}="{html.escape(v, quote=True)}"' for k, v in
                         (("id", s.id), ("kind", s.kind), ("speaker", s.speaker_role), ("t", _t(s))))
        rows.append(f"<segment {attrs}>{html.escape(s.text, quote=True)}</segment>")
    return "<sources>\n" + "\n".join(rows) + "\n</sources>"


def count_actions(segments: list[Segment]) -> int:
    return sum(1 for s in segments if s.speaker_role != "interviewer" and ACTION_VERBS.search(s.text))


def _parse(raw: str, seg_map: dict[str, Segment]) -> tuple[SOP | None, list[str]]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, [f"Output is not valid JSON: {exc}"]
    try:
        sop = SOP.model_validate(data)
    except ValidationError as exc:
        errs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:30]]
        return None, errs
    errs = structural_errors(sop, seg_map)
    return (None, errs) if errs else (sop, [])


def redact_obj(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


def generate_sop(req: GenerateRequest, llm: LLMClient) -> dict:
    segments = [s.model_copy(update={"text": redact(s.text)}) for s in req.segments]
    segments += [s.model_copy(update={"text": redact(s.text)}) for s in answer_segments(req)]
    seg_map = {s.id: s for s in segments}
    if count_actions(segments) < 2:
        raise InsufficientSource()

    schema = SOP.model_json_schema()
    system = load_prompt("system.md")
    title_hint = html.escape(redact(req.title_hint or ""), quote=True)
    user = load_prompt("compose.md").replace("{title_hint}", title_hint or "(none)") + "\n\n" + build_bundle(segments)

    raw = llm.complete_json(system, user, schema)
    sop, errors = _parse(raw, seg_map)
    repaired = False
    if sop is None:
        repaired = True
        repair_user = (load_prompt("repair.md")
                       .replace("{errors}", html.escape("\n".join(f"- {e}" for e in errors)))
                       .replace("{previous}", html.escape(raw[:20000]))) + "\n\n" + build_bundle(segments)
        raw = llm.complete_json(system, repair_user, schema)
        sop, errors = _parse(raw, seg_map)
        if sop is None:
            raise GenerationFailed()

    if len(sop.steps) < 2:
        raise InsufficientSource()

    validator_flags, val_questions = apply_evidence_rules(sop, seg_map)
    detector_hits, det_questions = run_detectors(sop, segments)
    rule_questions = gap_questions(sop, segments)
    llm_questions = []
    for q in sop.questions:
        q.origin = "llm"
        llm_questions.append(q)
    answered = [a.question for a in req.answers if a.answer.strip()]
    sop.questions = merge_questions([llm_questions, val_questions, det_questions, rule_questions], answered)
    sop.checklist = build_checklist(sop.questions)
    sop = SOP.model_validate(json.loads(sop.model_dump_json()))
    return {
        "sop": redact_obj(sop.model_dump()),
        "report": {
            "validator_flags": validator_flags,
            "detector_hits": detector_hits,
            "model": getattr(llm, "model", "unknown"),
            "prompt_version": PROMPT_VERSION,
            "repaired": repaired,
        },
    }
