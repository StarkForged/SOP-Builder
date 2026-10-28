import io
import struct
import wave

from app import ai
from app.main import app


def make_wav(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


def test_transcribe_offsets_timestamps(client):
    fake = ai.FakeASR([{"start": 0.0, "end": 3.2, "text": " How do you refund an order?", "avg_logprob": -0.1},
                       {"start": 3.5, "end": 9.0, "text": "I open the Orders page.", "avg_logprob": -0.2},
                       {"start": 9.0, "end": 9.5, "text": "  "}])
    app.dependency_overrides[ai.get_asr] = lambda: fake
    r = client.post("/api/transcribe", data={"offset_s": "100", "source_index": "2", "seq_start": "7",
                                             "consent": "true"},
                    files={"chunk": ("c.wav", make_wav(1), "audio/wav")})
    assert r.status_code == 200, r.text
    segs = r.json()["segments"]
    assert len(segs) == 2
    assert segs[0]["id"] == "S2-0007" and segs[0]["start_s"] == 100.0 and segs[0]["end_s"] == 103.2
    assert segs[1]["id"] == "S2-0008" and segs[1]["start_s"] == 103.5
    assert segs[0]["speaker_role"] == "interviewer" and segs[1]["speaker_role"] == "staff"
    assert 0 < segs[0]["confidence"] <= 1

    r = client.post("/api/transcribe", data={"source_index": "3", "consent": "true", "kind": "narration"},
                    files={"chunk": ("c.wav", make_wav(1), "audio/wav")})
    assert all(s["speaker_role"] == "narrator" and s["kind"] == "narration" for s in r.json()["segments"])

    r = client.post("/api/transcribe", data={"source_index": "3", "consent": "true"},
                    files={"chunk": ("c.wav", make_wav(102), "audio/wav")})
    assert r.status_code == 400 and r.json()["error"] == "chunk_too_long"
    r = client.post("/api/transcribe", data={"source_index": "3", "consent": "true"},
                    files={"chunk": ("c.wav", b"not a wav file", "audio/wav")})
    assert r.status_code == 400
    r = client.post("/api/transcribe", data={"source_index": "3", "consent": "true", "kind": "x"},
                    files={"chunk": ("c.wav", make_wav(1), "audio/wav")})
    assert r.status_code == 400


def test_media_requires_consent(client):
    app.dependency_overrides[ai.get_asr] = lambda: ai.FakeASR([])
    r = client.post("/api/transcribe", data={"source_index": "1"},
                    files={"chunk": ("c.wav", make_wav(1), "audio/wav")})
    assert r.status_code == 400 and r.json()["error"] == "consent_required"
    r = client.post("/api/transcribe", data={"source_index": "1", "consent": "false"},
                    files={"chunk": ("c.wav", make_wav(1), "audio/wav")})
    assert r.status_code == 400 and r.json()["error"] == "consent_required"


def test_frames_batch_limit_and_screen_text_segments(client):
    """Screen text is OCR'd in the browser; the server only redacts it and builds segments (no images)."""
    item = {"text": "Orders > Refund", "t": 0, "confidence": 91}
    r = client.post("/api/frames", json={"source_index": 5, "items": [item] * 41})
    assert r.status_code == 422  # more items than the browser's 40-keyframe cap

    items = [
        {"text": "Orders  >  Refund\n\nIssue refund", "t": 0, "confidence": 91.5},
        {"text": "blurry", "t": 3, "confidence": 42},       # low confidence -> dropped
        {"text": " ok ", "t": 6, "confidence": 99},         # < 3 characters -> dropped
        {"text": "Contact jane@example.com", "t": 9, "confidence": 80},
    ]
    r = client.post("/api/frames", json={"source_index": 5, "seq_start": 3, "items": items})
    assert r.status_code == 200
    segs = r.json()["segments"]
    assert [s["id"] for s in segs] == ["S5-0003", "S5-0004"]
    assert all(s["kind"] == "screen_text" and s["speaker_role"] == "system" for s in segs)
    assert [s["start_s"] for s in segs] == [0.0, 9.0]
    assert segs[0]["text"] == "Orders > Refund\nIssue refund" and segs[0]["confidence"] == 0.915
    assert "jane@example.com" not in segs[1]["text"] and "[EMAIL]" in segs[1]["text"]

    # images / multipart uploads are no longer accepted
    r = client.post("/api/frames", data={"source_index": "5"}, files=[("frames", ("f.jpg", JPEG, "image/jpeg"))])
    assert r.status_code == 422
    r = client.post("/api/frames", json={"source_index": 5, "items": [{**item, "image": "data:..."}]})
    assert r.status_code == 422
    r = client.post("/api/frames", json={"source_index": 5, "items": [{**item, "confidence": 150}]})
    assert r.status_code == 422
