"""Real client wrappers exercised with stub SDK objects (no network)."""

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


def test_llm_uses_json_object_mode_with_max_tokens():
    sdk = StubSDK([chat('```json\n{"a": 1}\n```')])
    assert ai.OpenAICompatLLM(sdk, "m").complete_json("s", "u", 2500) == '{"a": 1}'
    call = sdk.calls[0]
    assert call["response_format"] == {"type": "json_object"} and call["max_tokens"] == 2500
    assert call["temperature"] == 0 and "json_schema" not in str(call)
    with pytest.raises(ai.ProviderError):
        ai.OpenAICompatLLM(StubSDK([HTTPErr(500)]), "m").complete_json("s", "u", 10)
    with pytest.raises(ai.ProviderLimit):
        ai.OpenAICompatLLM(StubSDK([Exception("Quota exceeded")]), "m").complete_json("s", "u", 10)
    # Groq json_validate_failed (400) hands the model's text to the repair step instead of failing
    bad = HTTPErr(400)
    bad.body = {"error": {"code": "json_validate_failed", "failed_generation": '{"title": "x"'}}
    assert ai.OpenAICompatLLM(StubSDK([bad]), "m").complete_json("s", "u", 10) == '{"title": "x"'
    with pytest.raises(ai.ProviderError):
        ai.OpenAICompatLLM(StubSDK([HTTPErr(400)]), "m").complete_json("s", "u", 10)


def test_groq_asr_wrapper():
    resp = NS(segments=[{"start": 0, "end": 2, "text": "hi", "avg_logprob": -0.1}], text="hi")
    out = ai.GroqASR(StubSDK([resp]), "w").transcribe(b"wav", "c.wav")
    assert out == [{"start": 0, "end": 2, "text": "hi", "avg_logprob": -0.1}]
    assert ai.GroqASR(StubSDK([NS(segments=None, text="all")]), "w").transcribe(b"", "c")[0]["text"] == "all"
    with pytest.raises(ai.ProviderLimit):
        ai.GroqASR(StubSDK([HTTPErr(429)]), "w").transcribe(b"", "c")



def test_factories_require_keys(monkeypatch):
    for f in (ai.get_llm, ai.get_asr):
        with pytest.raises(ai.ProviderError):
            f()
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")
    llm = ai.get_llm()
    assert isinstance(llm, ai.OpenAICompatLLM) and llm.model == "openai/gpt-oss-120b"
    assert str(llm.sdk.base_url).startswith("https://api.groq.com/openai/v1")
    assert llm.sdk.api_key == "test-not-a-key"  # LLM_API_KEY defaults to GROQ_API_KEY
    assert isinstance(ai.get_asr(), ai.GroqASR) and not hasattr(ai, "get_vision")
    monkeypatch.setenv("LLM_API_KEY", "other-test-value")
    monkeypatch.setenv("LLM_MODEL", "m1")
    assert ai.get_llm().sdk.api_key == "other-test-value" and ai.get_llm().model == "m1"
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b")
    fb = ai.get_llm()
    assert isinstance(fb, ai.FallbackLLM) and fb.fallback.model == "openai/gpt-oss-20b"
    # same endpoint and key as the main LLM unless overridden
    assert fb.fallback.sdk.api_key == "other-test-value" and str(fb.fallback.sdk.base_url) == str(fb.primary.sdk.base_url)


def test_no_gemini_dependency():
    from app.config import get_settings

    assert not hasattr(get_settings(), "gemini_api_key") and not hasattr(get_settings(), "vision_model")
    for path in ("app/config.py", "app/ai.py", "DEPLOY.md", "public/index.html"):
        text = (ai.PROMPTS.parent / path).read_text(encoding="utf-8").upper()
        assert "GEMINI" not in text and "VISION_MODEL" not in text, path


def test_gpt_oss_uses_low_reasoning_effort():
    sdk = StubSDK([chat("{}"), chat("{}")])
    ai.OpenAICompatLLM(sdk, "openai/gpt-oss-120b").complete_json("s", "u", 100)
    ai.OpenAICompatLLM(sdk, "other-model").complete_json("s", "u", 100)
    assert sdk.calls[0]["reasoning_effort"] == "low" and "reasoning_effort" not in sdk.calls[1]


def test_fallback_on_429_and_5xx_only(caplog):
    ok = ai.FakeLLM(['{"ok": 1}'], model="fallback-model")
    for err in (ai.ProviderLimit(), ai.ProviderError("x", 503)):
        llm = ai.FallbackLLM(ai.FakeLLM([], model="main", error=err), ok)
        assert llm.complete_json("s", "u", 100) == '{"ok": 1}' and llm.model == "fallback-model"
    with pytest.raises(ai.ProviderError):
        ai.FallbackLLM(ai.FakeLLM([], error=ai.ProviderError("missing_api_key")), ok).complete_json("s", "u", 100)
    with pytest.raises(ai.ProviderLimit):  # fallback is tried once only
        ai.FallbackLLM(ai.FakeLLM([], error=ai.ProviderLimit()),
                       ai.FakeLLM([], error=ai.ProviderLimit())).complete_json("s", "u", 100)
    # real wrapper: status survives mapping, and logs carry status + message only (never keys)
    with caplog.at_level("WARNING", logger="sop.provider"):
        with pytest.raises(ai.ProviderError) as exc:
            ai.OpenAICompatLLM(StubSDK([HTTPErr(502)]), "m").complete_json("s", "SECRET USER TEXT", 100)
        try:
            ai.GroqASR(StubSDK([Exception("bad key " + "gsk" + "_abcdefghijklmnopqrstuvwx")]), "w").transcribe(b"", "c")
        except ai.ProviderError:
            pass
    assert exc.value.status == 502
    assert "status=502" in caplog.text and "SECRET USER TEXT" not in caplog.text
    assert "abcdefghijklmnop" not in caplog.text


def test_health_reports_env_flags_only(client, monkeypatch):
    r = client.get("/api/health").json()
    assert r["ready"] is False and r["env"]["GROQ_API_KEY"] is False
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")
    r = client.get("/api/health")
    assert r.json()["ready"] is True and r.json()["env"]["GROQ_API_KEY"] is True
    assert "test-not-a-key" not in r.text
    assert set(r.json()["env"]) == {"GROQ_API_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_FALLBACK_MODEL"}


def test_source_text_cap_returns_413(client, use_llm, monkeypatch, segments):
    fake = use_llm(ai.FakeLLM([{}]))
    monkeypatch.setenv("MAX_SOURCE_CHARS", "50")
    r = client.post("/api/generate", json={"segments": segments})
    assert r.status_code == 413 and r.json()["error"] == "source_too_long" and not fake.calls


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
