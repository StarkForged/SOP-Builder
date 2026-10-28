"""Settings read from environment variables at call time (no secrets in code)."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    groq_api_key: str
    llm_base_url: str
    llm_model: str
    vision_model: str
    groq_base_url: str
    groq_whisper_model: str
    max_request_bytes: int
    max_file_bytes: int
    max_frames: int
    max_frames_bytes: int
    max_chunk_seconds: int
    rate_limit_max: int
    rate_limit_window_s: int


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        gemini_api_key=e("GEMINI_API_KEY", ""),
        groq_api_key=e("GROQ_API_KEY", ""),
        llm_base_url=e("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        llm_model=e("LLM_MODEL", "gemini-2.5-flash"),
        vision_model=e("VISION_MODEL", "gemini-2.5-flash"),
        groq_base_url=e("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        groq_whisper_model=e("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
        max_request_bytes=_int("MAX_REQUEST_BYTES", 4_400_000),
        max_file_bytes=_int("MAX_FILE_BYTES", 4_000_000),
        max_frames=_int("MAX_FRAMES", 6),
        max_frames_bytes=_int("MAX_FRAMES_BYTES", 3_500_000),
        max_chunk_seconds=_int("MAX_CHUNK_SECONDS", 100),
        rate_limit_max=_int("RATE_LIMIT_MAX", 60),
        rate_limit_window_s=_int("RATE_LIMIT_WINDOW_S", 600),
    )


PROMPT_VERSION = "v1"
GENERATE_MAX_BYTES = 4_000_000
