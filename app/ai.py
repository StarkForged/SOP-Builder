"""LLM / ASR / vision clients (OpenAI-compatible: Gemini + Groq) and fakes for tests."""

import base64
import json
import re
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


class ProviderLimit(Exception):
    """Provider returned 429 / quota exhausted."""


class ProviderError(Exception):
    """Provider failed or is not configured."""


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


def _map_error(exc: Exception) -> Exception:
    status = getattr(exc, "status_code", None)
    msg = str(exc).lower()
    if status == 429 or "quota" in msg or "rate limit" in msg:
        return ProviderLimit()
    return ProviderError(type(exc).__name__)


class LLMClient(Protocol):
    model: str

    def complete_json(self, system: str, user: str, schema: dict) -> str: ...


class ASRClient(Protocol):
    def transcribe(self, audio: bytes, filename: str) -> list[dict]: ...


class VisionClient(Protocol):
    def read_frames(self, images: list[bytes]) -> list[str]: ...


def _sdk(base_url: str, api_key: str):
    if not api_key:
        raise ProviderError("missing_api_key")
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key, max_retries=1, timeout=120)


class OpenAICompatLLM:
    def __init__(self, sdk: Any, model: str):
        self.sdk, self.model = sdk, model

    def complete_json(self, system: str, user: str, schema: dict) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            try:
                resp = self.sdk.chat.completions.create(
                    model=self.model, messages=messages, temperature=0,
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "sop", "schema": schema}},
                )
            except Exception as exc:
                if getattr(exc, "status_code", None) != 400:
                    raise
                messages[1]["content"] = (
                    user + "\n\nReturn ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema)
                )
                resp = self.sdk.chat.completions.create(
                    model=self.model, messages=messages, temperature=0,
                    response_format={"type": "json_object"},
                )
        except Exception as exc:
            raise _map_error(exc) from None
        return strip_fences(resp.choices[0].message.content or "")


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


class GeminiVision:
    def __init__(self, sdk: Any, model: str):
        self.sdk, self.model = sdk, model

    def read_frames(self, images: list[bytes]) -> list[str]:
        content: list[dict] = [{"type": "text", "text": load_prompt("ocr.md").replace("{n}", str(len(images)))}]
        for img in images:
            mime = "image/png" if img.startswith(b"\x89PNG") else "image/jpeg"
            b64 = base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        try:
            resp = self.sdk.chat.completions.create(
                model=self.model, temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            raise _map_error(exc) from None
        try:
            data = json.loads(strip_fences(resp.choices[0].message.content or ""))
            frames = data.get("frames", []) if isinstance(data, dict) else data
            texts = {int(f.get("index", i)): str(f.get("text", "")) for i, f in enumerate(frames)}
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("bad_vision_output") from None
        return [texts.get(i, "") for i in range(len(images))]


def get_llm() -> LLMClient:
    s = get_settings()
    return OpenAICompatLLM(_sdk(s.llm_base_url, s.gemini_api_key), s.llm_model)


def get_asr() -> ASRClient:
    s = get_settings()
    return GroqASR(_sdk(s.groq_base_url, s.groq_api_key), s.groq_whisper_model)


def get_vision() -> VisionClient:
    s = get_settings()
    return GeminiVision(_sdk(s.llm_base_url, s.gemini_api_key), s.vision_model)


# ---------------------------------------------------------------- fakes (tests/evals)
class FakeLLM:
    """Returns canned responses in order; records every call."""

    def __init__(self, responses: list[Any], model: str = "fake-llm", error: Exception | None = None):
        self.responses = list(responses)
        self.model = model
        self.error = error
        self.calls: list[dict] = []

    def complete_json(self, system: str, user: str, schema: dict) -> str:
        self.calls.append({"system": system, "user": user})
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


class FakeVision:
    def __init__(self, texts: list[str]):
        self.texts = texts

    def read_frames(self, images: list[bytes]) -> list[str]:
        return [self.texts[i % len(self.texts)] for i in range(len(images))]
