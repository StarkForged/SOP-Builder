"""Settings read from environment variables at call time (no secrets in code)."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


GROQ_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_fallback_base_url: str
    llm_fallback_api_key: str
    llm_fallback_model: str
    groq_base_url: str
    groq_whisper_model: str
    max_request_bytes: int
    max_file_bytes: int
    max_chunk_seconds: int
    max_source_chars: int
    llm_token_budget: int
    llm_max_tokens: int
    llm_repair_max_tokens: int
    llm_source_tokens: int
    llm_segment_chars: int
    llm_max_wait_s: int
    rate_limit_max: int
    rate_limit_window_s: int
    fake_ai: bool


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        groq_api_key=e("GROQ_API_KEY", ""),
        llm_api_key=e("LLM_API_KEY", "") or e("GROQ_API_KEY", ""),
        llm_base_url=e("LLM_BASE_URL", "") or GROQ_URL,
        llm_model=e("LLM_MODEL", "") or "openai/gpt-oss-120b",
        # Fallback uses the same endpoint and key unless overridden; enabled only when LLM_FALLBACK_MODEL is set.
        llm_fallback_base_url=e("LLM_FALLBACK_BASE_URL", "") or e("LLM_BASE_URL", "") or GROQ_URL,
        llm_fallback_api_key=e("LLM_FALLBACK_API_KEY", "") or e("LLM_API_KEY", "") or e("GROQ_API_KEY", ""),
        llm_fallback_model=e("LLM_FALLBACK_MODEL", ""),
        groq_base_url=e("GROQ_BASE_URL", "") or GROQ_URL,
        groq_whisper_model=e("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
        max_request_bytes=_int("MAX_REQUEST_BYTES", 4_400_000),
        max_file_bytes=_int("MAX_FILE_BYTES", 4_000_000),
        max_chunk_seconds=_int("MAX_CHUNK_SECONDS", 100),
        # Hard cap on submitted source text (abuse guard); larger-than-budget sources are condensed below it.
        max_source_chars=_int("MAX_SOURCE_CHARS", 200_000),
        # Groq free tier counts prompt + reserved max_tokens against tokens-per-minute (8000 for gpt-oss-120b).
        llm_token_budget=_int("LLM_TOKEN_BUDGET", 6500),
        llm_max_tokens=_int("LLM_MAX_TOKENS", 2500),
        llm_repair_max_tokens=_int("LLM_REPAIR_MAX_TOKENS", 2000),
        llm_source_tokens=_int("LLM_SOURCE_TOKENS", 3000),
        llm_segment_chars=_int("LLM_SEGMENT_CHARS", 400),
        llm_max_wait_s=_int("LLM_MAX_WAIT_S", 20),
        rate_limit_max=_int("RATE_LIMIT_MAX", 60),
        rate_limit_window_s=_int("RATE_LIMIT_WINDOW_S", 600),
        fake_ai=e("FAKE_AI", "").lower() in ("1", "true", "yes"),
    )


PROMPT_VERSION = "v2"
GENERATE_MAX_BYTES = 4_000_000
