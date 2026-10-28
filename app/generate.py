"""SOP generation pipeline: redact -> bundle -> LLM -> validate/repair -> validators/detectors/gaps."""

import html
import json
import logging
import re
from typing import get_args

from pydantic import ValidationError

from app.ai import LLMClient, load_prompt
from app.budget import build_bundle, clean_segments, condense, est_tokens
from app.checklist import build_checklist
from app.config import PROMPT_VERSION, get_settings
from app.detectors import run_detectors
from app.gaps import gap_questions, merge_questions
from app.redact import redact
from app.schema import SOP, GenerateRequest, Question, Segment
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


log = logging.getLogger("sop.tokens")
CATEGORIES = "|".join(get_args(Question.model_fields["category"].annotation))


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


def _log_call(name: str, system: str, user: str, max_tokens: int, budget: int) -> None:
    prompt = est_tokens(system) + est_tokens(user)
    log.info("llm call=%s prompt_tokens~%d max_tokens=%d total~%d budget=%d",
             name, prompt, max_tokens, prompt + max_tokens, budget)


def compose_prompts(req: GenerateRequest, segments: list[Segment]) -> tuple[str, str, list[Segment], bool]:
    """System + user prompt sized so prompt + max_tokens stays within the token budget."""
    s = get_settings()
    system = load_prompt("system.md")
    title_hint = html.escape(redact(req.title_hint or "")[:200], quote=True)
    head = load_prompt("compose.md").replace("{categories}", CATEGORIES).replace("{title_hint}", title_hint or "(none)")
    room = s.llm_token_budget - s.llm_max_tokens - est_tokens(system) - est_tokens(head) - 20
    cleaned = clean_segments(segments, s.llm_segment_chars)
    sent, condensed = condense(cleaned, max(0, min(s.llm_source_tokens, room)))
    return system, head + "\n\n" + build_bundle(sent), sent, condensed


def repair_prompt(system: str, raw: str, errors: list[str], sent: list[Segment]) -> str:
    s = get_settings()
    head = (load_prompt("repair.md")
            .replace("{segment_ids}", ", ".join(x.id for x in sent))
            .replace("{errors}", html.escape("\n".join(f"- {e}" for e in errors[:20]))))
    room = s.llm_token_budget - s.llm_repair_max_tokens - est_tokens(system) - est_tokens(head) - 20
    return head.replace("{previous}", html.escape(raw[: max(0, room) * 4]))


def generate_sop(req: GenerateRequest, llm: LLMClient) -> dict:
    s = get_settings()
    segments = [x.model_copy(update={"text": redact(x.text)}) for x in req.segments]
    segments += [x.model_copy(update={"text": redact(x.text)}) for x in answer_segments(req)]
    seg_map = {x.id: x for x in segments}
    if count_actions(segments) < 2:
        raise InsufficientSource()

    system, user, sent, condensed = compose_prompts(req, segments)
    _log_call("compose", system, user, s.llm_max_tokens, s.llm_token_budget)
    raw = llm.complete_json(system, user, s.llm_max_tokens)
    sop, errors = _parse(raw, seg_map)
    repaired = False
    if sop is None:
        repaired = True
        repair_user = repair_prompt(system, raw, errors, sent)
        _log_call("repair", system, repair_user, s.llm_repair_max_tokens, s.llm_token_budget)
        raw = llm.complete_json(system, repair_user, s.llm_repair_max_tokens)
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
            "source_condensed": condensed,
            "segments_sent": len(sent),
            "segments_total": len(segments),
            "notes": ([f"The source was condensed to fit the free AI limit: the {len(sent)} most relevant of "
                       f"{len(segments)} segments were sent."] if condensed else []),
        },
    }
