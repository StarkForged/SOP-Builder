"""FastAPI app: stateless API + static page."""

import io
import json
import time
import wave
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import ai
from app.config import GENERATE_MAX_BYTES, get_settings
from app.export import filename, to_docx, to_markdown
from app.generate import GenerationFailed, InsufficientSource, generate_sop
from app.ingest_text import BadFile, extract_text, parse_transcript, parse_transcript_text
from app.redact import redact
from app.schema import ExportRequest, GenerateRequest, Segment
from app.segments import asr_segments, make_id, note_segments, turn_segments

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
    return err(429, "daily_free_limit", "Free AI limit reached, try again later.")


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


@app.get("/api/health")
def health():
    return {"ok": True}


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


@app.post("/api/frames")
async def frames(frames: list[UploadFile] = File(...), times: str = Form("[]"),
                 source_index: int = Form(..., ge=1, le=999), seq_start: int = Form(1, ge=1, le=9000),
                 consent: str = Form("false"), vision: ai.VisionClient = Depends(ai.get_vision)):
    s = get_settings()
    if (resp := need_consent(consent)) is not None:
        return resp
    if len(frames) > s.max_frames:
        return err(400, "too_many_frames", f"Send at most {s.max_frames} frames per request.")
    try:
        ts = [float(t) for t in json.loads(times)]
    except (ValueError, TypeError):
        return err(400, "bad_times", "times must be a JSON list of seconds.")
    images, total = [], 0
    for f in frames:
        data = await read_upload(f, s.max_frames_bytes)
        total += len(data)
        if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG")):
            raise BadFile("Frames must be JPEG or PNG images.")
        images.append(data)
    if total > s.max_frames_bytes:
        return err(413, "too_large", "Frame batch is too large (max 3.5 MB).")
    texts = vision.read_frames(images)
    segs, seq = [], seq_start
    for i, text in enumerate(texts):
        text = redact((text or "").strip())
        if not text:
            continue
        t = ts[i] if i < len(ts) else None
        segs.append(Segment(id=make_id(source_index, seq), source_index=source_index, kind="screen_text",
                            speaker_role="system", text=text[:20000], start_s=t, end_s=t,
                            confidence=None).model_dump())
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


if PUBLIC.exists():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="static")
