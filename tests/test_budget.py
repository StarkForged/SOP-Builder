"""Groq free-tier token budget: prompt sizing, condensing, compact repair, 429 retry-after and fallback."""

from types import SimpleNamespace as NS

import pytest

from app import ai
from app.budget import clean_segments, condense, est_tokens
from app.generate import CATEGORIES
from app.schema import Question, Segment
from tests.conftest import BASE_SOP, seg

BUDGET, COMPOSE, REPAIR = 6500, 2500, 2000


def total_tokens(call):
    return est_tokens(call["system"]) + est_tokens(call["user"]) + call["max_tokens"]


def word(i: int) -> str:
    out = ""
    for _ in range(4):
        out += "abcdefghijklmnopqrstuvwxy"[i % 25]
        i //= 25
    return "zz" + out  # letters only, never an action keyword


def filler(n: int, start: int = 1) -> list[dict]:
    return [seg(f"S2-{start + i:04d}", f"Casual chat about cricket and weather {word(i)} {word(i + 7)}") for i in range(n)]


def chat(content):
    return NS(choices=[NS(message=NS(content=content))])


class RateLimited(Exception):
    status_code = 429

    def __init__(self, headers=None):
        super().__init__("Rate limit reached for model on tokens per minute (TPM)")
        self.response = NS(headers=headers or {})


class StubSDK:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# ---------------------------------------------------------------- budget cap + compact prompts
def test_budget_cap_compose_and_compact_repair(client, use_llm, segments):
    big = segments + filler(400) + [seg("S3-0001", "Click Save and wait. " * 200)]
    invalid = '{"title": "x", "steps": "' + "y" * 40000
    fake = use_llm(ai.FakeLLM([invalid, BASE_SOP]))
    r = client.post("/api/generate", json={"segments": big})
    assert r.status_code == 200, r.text
    compose, repair = fake.calls
    assert compose["max_tokens"] == COMPOSE and repair["max_tokens"] == REPAIR
    assert total_tokens(compose) <= BUDGET and total_tokens(repair) <= BUDGET
    # no full JSON schema in the prompt: compact field list + tiny example instead
    assert "$defs" not in compose["user"] and '"properties"' not in compose["user"]
    assert "source_refs" in compose["user"] and "Shape example" in compose["user"]
    # repair sends errors + invalid JSON only, not the sources again
    assert "<sources>" not in repair["user"] and "<segment" not in repair["user"]
    assert "Errors:" in repair["user"] and "S1-0001" in repair["user"]
    assert len(repair["user"]) < len(invalid)


def test_budget_env_configurable(client, use_llm, segments, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "1800")
    monkeypatch.setenv("LLM_TOKEN_BUDGET", "4000")
    fake = use_llm(ai.FakeLLM([BASE_SOP]))
    assert client.post("/api/generate", json={"segments": segments + filler(300)}).status_code == 200
    assert fake.calls[0]["max_tokens"] == 1800 and total_tokens(fake.calls[0]) <= 4000


def test_prompt_categories_match_schema():
    from typing import get_args

    assert CATEGORIES.split("|") == list(get_args(Question.model_fields["category"].annotation))


# ---------------------------------------------------------------- condensing
def test_clean_segments_drops_empty_duplicates_and_caps_length():
    segs = [Segment(**s) for s in [
        seg("S1-0001", "Open   the\n  Orders page"), seg("S1-0002", "open the orders page"), seg("S1-0003", "  "),
        seg("S1-0004", "Click Save. " * 100)]]
    out = clean_segments(segs, 400)
    assert [s.id for s in out] == ["S1-0001", "S1-0004"]
    assert out[0].text == "Open the Orders page" and len(out[1].text) == 400 and out[1].text.endswith("…")


def test_small_source_not_condensed(client, use_llm, segments):
    fake = use_llm(ai.FakeLLM([BASE_SOP]))
    body = client.post("/api/generate", json={"segments": segments}).json()
    assert body["report"]["source_condensed"] is False and body["report"]["notes"] == []
    assert all(s["id"] in fake.calls[0]["user"] for s in segments)


def test_large_source_condensed_to_relevant_segments(client, use_llm, segments):
    interviewer = seg("S4-0001", "So what happens next, can you tell me more?", role="interviewer")
    big = segments + filler(400) + [interviewer]
    answers = [{"question_id": "q-1", "question": "Who approves?", "answer": "The store manager approves."}]
    fake = use_llm(ai.FakeLLM([BASE_SOP]))
    body = client.post("/api/generate", json={"segments": big, "answers": answers}).json()
    report, user = body["report"], fake.calls[0]["user"]
    assert report["source_condensed"] is True and report["segments_sent"] < report["segments_total"]
    assert "condensed" in report["notes"][0]
    assert est_tokens(user[user.index("<sources>"):]) <= 3000
    for s in segments:  # actions, conditions and warnings are kept
        assert f'id="{s["id"]}"' in user
    assert 'id="S0-0001"' in user  # reviewer answers always kept
    assert "cricket" not in user and 'id="S4-0001"' not in user
    # segments keep their original order
    assert user.index('id="S1-0001"') < user.index('id="S1-0006"') < user.index('id="S0-0001"')


def test_condense_respects_budget_directly():
    segs = [Segment(**s) for s in seg_list(60)]
    kept, condensed = condense(segs, 300)
    assert condensed and 0 < len(kept) < 60
    assert sum(est_tokens(f'<segment id="{s.id}">{s.text}</segment>') for s in kept) <= 300


def seg_list(n):
    return [seg(f"S1-{i + 1:04d}", f"Click the Save button and verify the result {word(i)}") for i in range(n)]


# ---------------------------------------------------------------- 429 handling
@pytest.mark.parametrize("value,seconds", [("3", 3.0), ("7.66s", 7.66), ("1m30.5s", 90.5), ("450ms", 0.45),
                                           ("2h", 7200.0), ("", None), ("soon", None)])
def test_parse_duration(value, seconds):
    assert ai.parse_duration(value) == seconds


def test_short_retry_after_waits_and_retries_once():
    waits = []
    sdk = StubSDK([RateLimited({"retry-after": "3", "x-ratelimit-reset-tokens": "7.5s"}), chat('{"ok": 1}')])
    llm = ai.OpenAICompatLLM(sdk, "openai/gpt-oss-120b", max_wait_s=20, sleep=waits.append)
    assert llm.complete_json("s", "u", 2500) == '{"ok": 1}'
    assert waits == [7.5] and len(sdk.calls) == 2  # waits for the longer of the two headers
    assert sdk.calls[0]["reasoning_effort"] == "low" and sdk.calls[0]["max_tokens"] == 2500

    waits.clear()
    sdk = StubSDK([RateLimited({"retry-after": "2"}), RateLimited({"retry-after": "2"})])
    with pytest.raises(ai.ProviderLimit):  # retried once only
        ai.OpenAICompatLLM(sdk, "m", sleep=waits.append).complete_json("s", "u", 10)
    assert waits == [2.0] and len(sdk.calls) == 2


def test_long_or_unknown_wait_does_not_sleep():
    for headers in ({"x-ratelimit-reset-tokens": "45s"}, {"retry-after": "120"}, {}):
        waits = []
        sdk = StubSDK([RateLimited(headers)])
        with pytest.raises(ai.ProviderLimit) as exc:
            ai.OpenAICompatLLM(sdk, "m", max_wait_s=20, sleep=waits.append).complete_json("s", "u", 10)
        assert waits == [] and len(sdk.calls) == 1
        assert exc.value.retry_after == ai.retry_after_seconds(RateLimited(headers))


def test_long_wait_switches_to_fallback_and_stays_there(client, use_llm, segments):
    main_sdk = StubSDK([RateLimited({"x-ratelimit-reset-tokens": "50s"})])
    fb_sdk = StubSDK([chat('{"title": "bad"}'), chat(__import__("json").dumps(BASE_SOP))])
    llm = ai.FallbackLLM(ai.OpenAICompatLLM(main_sdk, "openai/gpt-oss-120b", sleep=lambda s: None),
                         ai.OpenAICompatLLM(fb_sdk, "openai/gpt-oss-20b", sleep=lambda s: None))
    use_llm(llm)
    r = client.post("/api/generate", json={"segments": segments})
    assert r.status_code == 200 and r.json()["report"]["model"] == "openai/gpt-oss-20b"
    assert len(main_sdk.calls) == 1 and len(fb_sdk.calls) == 2  # repair also went to the fallback


def test_fallback_also_limited_returns_friendly_429(client, use_llm, segments):
    def limited():
        return ai.OpenAICompatLLM(StubSDK([RateLimited({"retry-after": "60"})]), "m", sleep=lambda s: None)

    use_llm(ai.FallbackLLM(limited(), limited()))
    r = client.post("/api/generate", json={"segments": segments})
    assert r.status_code == 429
    assert r.json() == {"error": "daily_free_limit", "message": "Free AI limit reached, try again in a minute."}


def test_fallback_model_env_wiring(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("LLM_MAX_WAIT_S", "15")
    llm = ai.get_llm()
    assert llm.primary.model == "openai/gpt-oss-120b" and llm.fallback.model == "openai/gpt-oss-20b"
    assert llm.primary.max_wait_s == 15 and llm.primary.sdk.max_retries == 0


# ---------------------------------------------------------------- logging
def test_token_estimates_logged_as_numbers_only(client, use_llm, segments, caplog):
    use_llm(ai.FakeLLM([BASE_SOP]))
    with caplog.at_level("INFO", logger="sop.tokens"):
        client.post("/api/generate", json={"segments": segments})
    lines = [r.getMessage() for r in caplog.records if r.name == "sop.tokens"]
    assert lines and "prompt_tokens~" in lines[0] and "max_tokens=2500" in lines[0]
    assert "refund" not in " ".join(lines).lower()
