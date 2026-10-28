"""LLM / ASR clients (OpenAI-compatible, Groq free tier by default) and fakes for tests.

Screen text is read in the browser (Tesseract.js); no images reach the server or any AI service.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings
from app.redact import redact

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
log = logging.getLogger("sop.provider")


class ProviderLimit(Exception):
    """Provider returned 429 / quota exhausted. retry_after = seconds the provider asked us to wait, if known."""

    status = 429

    def __init__(self, retry_after: float | None = None):
        super().__init__("rate_limited")
        self.retry_after = retry_after


class ProviderError(Exception):
    """Provider failed or is not configured."""

    def __init__(self, msg: str = "", status: int | None = None):
        super().__init__(msg)
        self.status = status


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def strip_fences(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return m.group(1) if m else text


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(value: str | None) -> float | None:
    """'7.5' / '7.66s' / '1m30.5s' / '450ms' / '2h' -> seconds."""
    if not value:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    parts = _DURATION.findall(value)
    if not parts:
        return None
    scale = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(n) * scale[u] for n, u in parts)


def retry_after_seconds(exc: Exception) -> float | None:
    """Longest wait the provider asked for (retry-after / x-ratelimit-reset-tokens headers)."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    waits = [parse_duration(headers.get(h)) for h in ("retry-after", "x-ratelimit-reset-tokens")]
    waits = [w for w in waits if w is not None]
    return max(waits) if waits else None


def _failed_generation(exc: Exception) -> str | None:
    """Groq returns 400 json_validate_failed with the model's text; hand it to the repair step."""
    body = getattr(exc, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    if isinstance(err, dict) and err.get("code") == "json_validate_failed":
        return str(err.get("failed_generation") or "")
    return None


def _map_error(exc: Exception) -> Exception:
    status = getattr(exc, "status_code", None)
    msg = str(exc)
    # Log provider status + error message only: never keys, prompts or user text.
    log.warning("provider error status=%s message=%s", status, redact(msg)[:200])
    low = msg.lower()
    if status == 429 or "quota" in low or "rate limit" in low:
        return ProviderLimit(retry_after_seconds(exc))
    return ProviderError(type(exc).__name__, status)


class LLMClient(Protocol):
    model: str

    def complete_json(self, system: str, user: str, max_tokens: int) -> str: ...


class ASRClient(Protocol):
    def transcribe(self, audio: bytes, filename: str) -> list[dict]: ...


def _sdk(base_url: str, api_key: str, max_retries: int = 1):
    if not api_key:
        raise ProviderError("missing_api_key")
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key, max_retries=max_retries, timeout=120)


class OpenAICompatLLM:
    """JSON-mode chat completion. On 429 waits once if the provider asks for <= max_wait_s, else raises."""

    def __init__(self, sdk: Any, model: str, max_wait_s: float = 20, sleep=time.sleep):
        self.sdk, self.model, self.max_wait_s, self.sleep = sdk, model, max_wait_s, sleep

    def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        try:
            return self._call(system, user, max_tokens)
        except ProviderLimit as lim:
            if lim.retry_after is None or lim.retry_after > self.max_wait_s:
                raise
            log.warning("rate limited; waiting %.1f s then retrying once", lim.retry_after)
            self.sleep(lim.retry_after)
            return self._call(system, user, max_tokens)

    def _call(self, system: str, user: str, max_tokens: int) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # gpt-oss models reason before answering; "low" keeps hidden reasoning from eating the token budget.
        extra = {"reasoning_effort": "low"} if "gpt-oss" in self.model else {}
        try:
            resp = self.sdk.chat.completions.create(
                model=self.model, messages=messages, temperature=0, max_tokens=max_tokens,
                response_format={"type": "json_object"}, **extra,
            )
        except Exception as exc:
            failed = _failed_generation(exc) if getattr(exc, "status_code", None) == 400 else None
            if failed is not None:
                log.warning("provider status=400 json_validate_failed; sending output to repair")
                return strip_fences(failed)
            raise _map_error(exc) from None
        return strip_fences(resp.choices[0].message.content or "")


class FallbackLLM:
    """Main model first; on 429/5xx switch to the fallback model (separate limit) for the rest of the request."""

    def __init__(self, primary: LLMClient, fallback: LLMClient):
        self.primary, self.fallback = primary, fallback
        self.active = primary
        self.model = primary.model

    def complete_json(self, system: str, user: str, max_tokens: int) -> str:
        if self.active is self.fallback:
            return self.fallback.complete_json(system, user, max_tokens)
        try:
            return self.primary.complete_json(system, user, max_tokens)
        except (ProviderLimit, ProviderError) as exc:
            status = exc.status or 0
            if not (status == 429 or status >= 500):
                raise
            log.warning("main model failed status=%s; switching to fallback model", status)
            self.active = self.fallback
            self.model = self.fallback.model
            return self.fallback.complete_json(system, user, max_tokens)


class GroqASR:
    def __init__(self, sdk: Any, model: str):
        self.sdk, self.model = sdk, model

    def transcribe(self, audio: bytes, filename: str) -> list[dict]:
        try:
            resp = self.sdk.audio.transcriptions.create(
                model=self.model, file=(filename, audio), response_format="verbose_json", temperature=0,
            )
        except Exception as exc:
            raise _map_error(exc) from None
        segs = _get(resp, "segments") or []
        if not segs and _get(resp, "text"):
            return [{"start": 0.0, "end": 0.0, "text": _get(resp, "text")}]
        return [{"start": _get(s, "start", 0.0), "end": _get(s, "end", 0.0), "text": _get(s, "text", ""),
                 "avg_logprob": _get(s, "avg_logprob")} for s in segs]


def get_llm() -> LLMClient:
    s = get_settings()
    if s.fake_ai:
        return DemoLLM()
    # max_retries=0: rate-limit waits are handled here (retry-after aware), not hidden in the SDK.
    main = OpenAICompatLLM(_sdk(s.llm_base_url, s.llm_api_key, 0), s.llm_model, s.llm_max_wait_s)
    if s.llm_fallback_model:
        return FallbackLLM(main, OpenAICompatLLM(_sdk(s.llm_fallback_base_url, s.llm_fallback_api_key, 0),
                                                 s.llm_fallback_model, s.llm_max_wait_s))
    return main


def get_asr() -> ASRClient:
    s = get_settings()
    if s.fake_ai:
        return FakeASR(DEMO_TRANSCRIPT)
    return GroqASR(_sdk(s.groq_base_url, s.groq_api_key), s.groq_whisper_model)


# ---------------------------------------------------------------- fakes (tests/evals)
class FakeLLM:
    """Returns canned responses in order; records every call."""

    def __init__(self, responses: list[Any], model: str = "fake-llm", error: Exception | None = None):
        self.responses = list(responses)
        self.model = model
        self.error = error
        self.calls: list[dict] = []

    def complete_json(self, system: str, user: str, max_tokens: int = 0) -> str:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if self.error:
            raise self.error
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r if isinstance(r, str) else json.dumps(r)


class FakeASR:
    def __init__(self, segments: list[dict], error: Exception | None = None):
        self.segments, self.error, self.calls = segments, error, 0

    def transcribe(self, audio: bytes, filename: str) -> list[dict]:
        self.calls += 1
        if self.error:
            raise self.error
        return [dict(s) for s in self.segments]


# ---------------------------------------------------------------- local demo mode (FAKE_AI=1, no keys)
DEMO_TRANSCRIPT = [
    {"start": 0.0, "end": 4.0, "text": "First I log in to the admin dashboard."},
    {"start": 4.0, "end": 9.0, "text": "Then I open the order and click Issue refund."},
]
_SEG = re.compile(r'<segment id="([^"]+)" kind="([^"]+)" speaker="([^"]+)" t="[^"]*">(.*?)</segment>', re.S)
_ACTION = re.compile(
    r"(?i)\b(log|open|click|enter|select|search|check|verify|confirm|submit|send|email|approve|issue|refund|"
    r"print|scan|upload|save|call|record|update|create|close|go|choose|fill|review|pay|wait|need)\w*\b"
)
_WARN = re.compile(r"(?i)\b(never|do not|don't|careful|warning)\b")
_DECISION = re.compile(r"(?i)\b(if|above|over|below|unless)\b")
_CHECK = re.compile(r"(?i)(check|verify|confirm)\b")


class DemoLLM:
    """Deterministic stand-in for local demos: turns each action-like source line into a step."""

    model = "demo (FAKE_AI)"

    def complete_json(self, system: str, user: str, max_tokens: int = 0) -> str:
        import html

        steps, warnings = [], []
        for sid, kind, speaker, raw in _SEG.findall(user):
            text = html.unescape(raw).strip()
            if speaker in ("interviewer", "reviewer") or not _ACTION.search(text) or len(text.split()) < 4:
                continue
            ref = [{"segment_id": sid, "quote": text[:200]}]
            if _WARN.search(text):
                warnings.append({"id": f"warn-{len(warnings) + 1}", "text": text, "severity": "caution",
                                 "source_refs": ref, "status": "supported"})
                continue
            n = len(steps) + 1
            decision = _DECISION.search(text)
            check = _CHECK.match(text)
            steps.append({
                "id": f"step-{n}", "number": n, "kind": "decision" if decision else "verification" if check else "action",
                "actor": None, "action": text, "condition": text if decision else None, "expected_result": None,
                "ui_elements": [], "source_refs": ref,
                "status": "inferred" if kind == "screen_text" else "supported",
            })
        hint = re.search(r"Title hint \(data, may be empty\): (.*)", user)
        title = html.unescape(hint.group(1)).strip() if hint else ""
        title = title if title and title != "(none)" else "Standard operating procedure"
        return json.dumps({"title": title, "purpose": "Procedure drafted from the sources (demo mode, no AI).",
                           "prerequisites": [], "steps": steps or [], "warnings": warnings, "questions": [],
                           "checklist": []})
