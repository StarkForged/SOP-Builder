"""FastAPI app: stateless API + static page."""

import io
import logging
import mimetypes
import os
import time
import wave
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app import ai
from app.config import GENERATE_MAX_BYTES, get_settings
from app.export import filename, to_docx, to_markdown
from app.generate import GenerationFailed, InsufficientSource, generate_sop
from app.ingest_text import BadFile, extract_text, parse_transcript, parse_transcript_text
from app.redact import redact
from app.schema import ExportRequest, GenerateRequest, Segment
from app.segments import asr_segments, make_id, note_segments, turn_segments

# Windows registries sometimes map .js to text/plain; ES modules need a JavaScript MIME type.
mimetypes.add_type("text/javascript", ".js")
# App logs (provider status, token estimates; never keys or user text) go to stdout for Vercel logs.
_log = logging.getLogger("sop")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

SAMPLE = {
    "title": "Customer refund processing",
    "text": (
        "Customer refund processing\n"
        "- Log in to the admin dashboard\n"
        "- Open the Orders page and search for the order number\n"
        "- Check the order was paid and delivered\n"
        "- Refunds above \u20b95,000 need manager approval\n"
        '- Click "Issue refund" and enter the refund amount\n'
        "- Never refund to a different card than the one used for payment\n"
        "- The refund usually reaches the customer soon\n"
        "- Email the customer the refund confirmation\n"
    ),
}

PUBLIC = Path(__file__).resolve().parent.parent / "public"
app = FastAPI(title="SOP Builder", docs_url=None, redoc_url=None, openapi_url=None)
_hits: dict[str, deque] = defaultdict(deque)


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


@app.middleware("http")
async def limits(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        s = get_settings()
        try:
            size = int(request.headers.get("content-length") or 0)
        except ValueError:
            size = 0
        if size > s.max_request_bytes:
            return err(413, "too_large", "Request is too large (max 4 MB). Split the file or shorten the text.")
        ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown")
        now, q = time.time(), _hits[ip]
        while q and q[0] < now - s.rate_limit_window_s:
            q.popleft()
        if len(q) >= s.rate_limit_max:
            return err(429, "rate_limited", "Too many requests. Please wait a few minutes.")
        q.append(now)
    return await call_next(request)


@app.exception_handler(ai.ProviderLimit)
async def _limit(_: Request, __: Exception):
    return err(429, "daily_free_limit", "Free AI limit reached, try again in a minute.")


@app.exception_handler(ai.ProviderError)
async def _provider(_: Request, __: Exception):
    return err(502, "provider_error", "The AI service failed or is not configured. Try again later.")


@app.exception_handler(BadFile)
async def _badfile(_: Request, exc: Exception):
    return err(400, "bad_file", str(exc))


@app.exception_handler(RequestValidationError)
async def _invalid(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []))
    return err(422, "invalid_request", f"Invalid request at {where}: {first.get('msg', '')}")


async def read_upload(f: UploadFile, limit: int) -> bytes:
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise BadFile(f"File is too large (max {limit // 1_000_000} MB).")
    return data


def need_consent(consent: str) -> JSONResponse | None:
    if str(consent).lower() not in ("true", "1", "yes", "on"):
        return err(400, "consent_required", "Please tick the consent box before uploading media.")
    return None


@app.get("/api/sample")
def sample():
    """Built-in example notes for the "Try a sample" button."""
    return SAMPLE


@app.get("/api/health")
def health():
    """Which env vars are set (true/false only, never values)."""
    e = os.environ.get
    env = {name: bool(e(name)) for name in ("GROQ_API_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_FALLBACK_MODEL")}
    ready = env["GROQ_API_KEY"]  # LLM_API_KEY defaults to GROQ_API_KEY
    return {"ok": True, "ready": ready, "env": env}


@app.post("/api/notes")
async def notes(source_index: int = Form(..., ge=1, le=999), kind: str = Form("notes"),
                text: str = Form(""), file: UploadFile | None = File(None)):
    s = get_settings()
    if kind not in ("notes", "transcript"):
        return err(400, "bad_kind", "kind must be 'notes' or 'transcript'.")
    data = await read_upload(file, s.max_file_bytes) if file is not None and file.filename else b""
    if len(text.encode()) > s.max_file_bytes:
        return err(413, "too_large", "Text is too long (max 4 MB).")
    if kind == "transcript":
        turns = parse_transcript(file.filename, data) if data else parse_transcript_text(text)
        segs, speakers = turn_segments(turns, source_index)
    else:
        raw = extract_text(file.filename, data) if data else text
        segs, speakers = note_segments(raw, source_index), {}
    if not segs:
        return err(400, "empty", "No text found in this source.")
    out = [seg.model_copy(update={"text": redact(seg.text)}).model_dump() for seg in segs]
    return {"segments": out, "speakers": {k: redact(v) for k, v in speakers.items()}}


def wav_seconds(data: bytes) -> float:
    if not (data[:4] == b"RIFF" and data[8:12] == b"WAVE"):
        raise BadFile("Audio chunk must be a WAV file.")
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception as exc:
        raise BadFile("Could not read the WAV chunk.") from exc


@app.post("/api/transcribe")
async def transcribe(chunk: UploadFile = File(...), offset_s: float = Form(0, ge=0, le=7200),
                     source_index: int = Form(..., ge=1, le=999), seq_start: int = Form(1, ge=1, le=9000),
                     kind: str = Form("transcript_turn"), consent: str = Form("false"),
                     asr: ai.ASRClient = Depends(ai.get_asr)):
    s = get_settings()
    if (resp := need_consent(consent)) is not None:
        return resp
    if kind not in ("transcript_turn", "narration"):
        return err(400, "bad_kind", "kind must be transcript_turn or narration.")
    data = await read_upload(chunk, s.max_request_bytes)
    if wav_seconds(data) > s.max_chunk_seconds + 1:
        return err(400, "chunk_too_long", f"Audio chunks must be at most {s.max_chunk_seconds} seconds.")
    items = asr.transcribe(data, "chunk.wav")
    segs = asr_segments(items, source_index, seq_start, offset_s, kind)
    return {"segments": [seg.model_copy(update={"text": redact(seg.text)}).model_dump() for seg in segs]}


MAX_SCREEN_ITEMS = 40  # matches the browser's keyframe cap
MIN_OCR_CONFIDENCE = 50


class ScreenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=5000)
    t: float | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=100)


class ScreenTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_index: int = Field(ge=1, le=999)
    seq_start: int = Field(default=1, ge=1, le=9000)
    items: list[ScreenItem] = Field(max_length=MAX_SCREEN_ITEMS)


@app.post("/api/frames")
async def frames(req: ScreenTextRequest):
    """Screen text already read in the browser (OCR) -> redacted screen_text segments. No images accepted."""
    segs, seq = [], req.seq_start
    for item in req.items:
        lines = (" ".join(line.split()) for line in item.text.splitlines())
        text = redact("\n".join(line for line in lines if line))
        if item.confidence < MIN_OCR_CONFIDENCE or len(text.strip()) < 3:
            continue
        segs.append(Segment(id=make_id(req.source_index, seq), source_index=req.source_index, kind="screen_text",
                            speaker_role="system", text=text, start_s=item.t, end_s=item.t,
                            confidence=round(item.confidence / 100, 3)).model_dump())
        seq += 1
    return {"segments": segs}


@app.post("/api/generate")
async def generate(request: Request, llm: ai.LLMClient = Depends(ai.get_llm)):
    body = await request.body()
    if len(body) > GENERATE_MAX_BYTES:
        return err(413, "too_large", "Too much source material (max 4 MB). Remove some sources and retry.")
    try:
        req = GenerateRequest.model_validate_json(body)
    except ValueError as exc:
        return err(422, "invalid_request", f"Invalid request: {str(exc)[:300]}")
    limit = get_settings().max_source_chars
    size = sum(len(s.text) for s in req.segments) + sum(len(a.question) + len(a.answer) for a in req.answers)
    if size > limit:
        return err(413, "source_too_long",
                   f"Sources are too long for the free AI tier ({size:,} characters, max {limit:,}). "
                   "Remove or shorten a source and try again.")
    try:
        return generate_sop(req, llm)
    except InsufficientSource:
        return err(422, "insufficient_source",
                   "The sources don't describe a procedure (need at least 2 actions). Add notes, an "
                   "interview or a narrated recording that explains the steps.")
    except GenerationFailed:
        return err(502, "generation_failed", "The AI returned an invalid SOP twice. Please try again.")


@app.post("/api/export")
async def export(req: ExportRequest):
    if req.format == "docx":
        content = to_docx(req)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        content = to_markdown(req).encode("utf-8")
        media = "text/markdown; charset=utf-8"
    name = filename(req, req.format)
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(PUBLIC / "index.html")


# On Vercel, public/ is served by the CDN and must not be mounted; mount it only for local runs.
if PUBLIC.exists() and not os.environ.get("VERCEL"):
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="static")
