"""Real client wrappers exercised with stub SDK objects (no network)."""

import json
from types import SimpleNamespace as NS

import pytest

from app import ai


class StubSDK:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))
        self.audio = NS(transcriptions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def chat(content):
    return NS(choices=[NS(message=NS(content=content))])


class HTTPErr(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


def test_llm_json_schema_then_fallback():
    sdk = StubSDK([chat('```json\n{"a": 1}\n```')])
    assert ai.OpenAICompatLLM(sdk, "m").complete_json("s", "u", {"type": "object"}) == '{"a": 1}'
    assert sdk.calls[0]["response_format"]["type"] == "json_schema" and sdk.calls[0]["temperature"] == 0
    sdk = StubSDK([HTTPErr(400), chat('{"b": 2}')])
    assert ai.OpenAICompatLLM(sdk, "m").complete_json("s", "u", {"type": "object"}) == '{"b": 2}'
    assert sdk.calls[1]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in sdk.calls[1]["messages"][1]["content"]
    with pytest.raises(ai.ProviderError):
        ai.OpenAICompatLLM(StubSDK([HTTPErr(500)]), "m").complete_json("s", "u", {})
    with pytest.raises(ai.ProviderLimit):
        ai.OpenAICompatLLM(StubSDK([Exception("Quota exceeded")]), "m").complete_json("s", "u", {})


def test_groq_asr_and_vision_wrappers():
    resp = NS(segments=[{"start": 0, "end": 2, "text": "hi", "avg_logprob": -0.1}], text="hi")
    out = ai.GroqASR(StubSDK([resp]), "w").transcribe(b"wav", "c.wav")
    assert out == [{"start": 0, "end": 2, "text": "hi", "avg_logprob": -0.1}]
    assert ai.GroqASR(StubSDK([NS(segments=None, text="all")]), "w").transcribe(b"", "c")[0]["text"] == "all"
    with pytest.raises(ai.ProviderLimit):
        ai.GroqASR(StubSDK([HTTPErr(429)]), "w").transcribe(b"", "c")

    sdk = StubSDK([chat(json.dumps({"frames": [{"index": 1, "text": "Save"}, {"index": 0, "text": "Home"}]}))])
    assert ai.GeminiVision(sdk, "v").read_frames([b"\x89PNG..", b"\xff\xd8\xff"]) == ["Home", "Save"]
    content = sdk.calls[0]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    with pytest.raises(ai.ProviderError):
        ai.GeminiVision(StubSDK([chat("nope")]), "v").read_frames([b"x"])
    with pytest.raises(ai.ProviderLimit):
        ai.GeminiVision(StubSDK([HTTPErr(429)]), "v").read_frames([b"x"])


def test_factories_require_keys(monkeypatch):
    for f in (ai.get_llm, ai.get_asr, ai.get_vision):
        with pytest.raises(ai.ProviderError):
            f()
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")
    assert ai.get_llm().model == "gemini-2.5-flash"
    assert isinstance(ai.get_asr(), ai.GroqASR) and isinstance(ai.get_vision(), ai.GeminiVision)


def test_missing_key_returns_502(client, segments):
    r = client.post("/api/generate", json={"segments": segments})
    assert r.status_code == 502 and r.json()["error"] == "provider_error"


def test_rate_limit(client, monkeypatch):
    from app import main

    monkeypatch.setenv("RATE_LIMIT_MAX", "2")
    main._hits.clear()
    codes = [client.get("/api/health", headers={"x-forwarded-for": "9.9.9.9"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    main._hits.clear()
