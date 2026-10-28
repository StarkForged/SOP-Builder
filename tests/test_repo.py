import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(GEMINI|GROQ|LLM|LLM_FALLBACK)_API_KEY\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "reports", ".claude", ".venv", "venv"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        files = [ROOT / f for f in out.splitlines() if f]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p for p in ROOT.rglob("*") if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)
            and p.name not in ("CLAUDE.md", "KICKOFF_PROMPT.md")]


def test_no_secrets_in_tracked_files():
    files = tracked_files()
    assert files
    for f in files:
        assert not re.match(r"^\.env", f.name), f"env file must not be committed: {f}"
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for pat in KEY_PATTERNS:
            assert not pat.search(text), f"possible secret in {f.relative_to(ROOT)}"


def test_requirements_has_no_local_only_heavy_packages():
    reqs = (ROOT / "requirements.txt").read_text().lower()
    names = {re.split(r"[=<>\[ ]", line)[0].strip() for line in reqs.splitlines() if line.strip()
             and not line.startswith("#")}
    banned = {"torch", "tensorflow", "openai-whisper", "whisper", "faster-whisper", "transformers",
              "opencv-python", "moviepy", "ffmpeg-python", "pydub", "numpy", "scipy", "pytest", "ruff"}
    assert not names & banned
    assert {"fastapi", "pydantic", "openai", "python-docx", "pypdf", "webvtt-py", "rapidfuzz", "filetype",
            "python-multipart"} <= names
    assert all("==" in line for line in reqs.splitlines() if line.strip())
    assert not (ROOT / "pyproject.toml").exists()
