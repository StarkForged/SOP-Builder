"""Run eval cases against the API with canned fake LLM responses (no network, no keys)."""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.ingest_text import parse_transcript_text  # noqa: E402
from app.main import app  # noqa: E402
from app.segments import note_segments, turn_segments  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
FAKES = Path(__file__).resolve().parent / "fake_responses"


def build_segments(sources: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, src in enumerate(sources, start=1):
        if src["type"] == "notes":
            out += [s.model_dump() for s in note_segments(src["text"], i)]
        elif src["type"] == "transcript":
            segs, speakers = turn_segments(parse_transcript_text(src["text"]), i)
            for s in segs:
                d = s.model_dump()
                if speakers.get(s.id) == src.get("interviewer"):
                    d["speaker_role"] = "interviewer"
                out.append(d)
        else:
            out += src["segments"]
    return out


def sop_text(sop: dict) -> str:
    parts = [sop["title"], sop["purpose"]]
    for p in sop["prerequisites"]:
        parts.append(p["text"])
    for s in sop["steps"]:
        parts += [s["action"], s["condition"] or "", s["expected_result"] or "", *s["ui_elements"]]
    parts += [w["text"] for w in sop["warnings"]]
    return "\n".join(parts)


def run_case(path: Path) -> tuple[bool, list[str]]:
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    exp = case["expect"]
    fake = ai.FakeLLM([json.loads((FAKES / case["fake_response"]).read_text(encoding="utf-8"))])
    app.dependency_overrides[ai.get_llm] = lambda: fake
    try:
        with TestClient(app) as client:
            r = client.post("/api/generate", json={"title_hint": case.get("title_hint") or "",
                                                   "segments": build_segments(case["sources"]), "answers": []})
    finally:
        app.dependency_overrides.clear()
    problems = []
    if r.status_code != exp["status"]:
        return False, [f"status {r.status_code} != {exp['status']}: {r.text[:200]}"]
    if exp["status"] != 200:
        return True, []
    body = r.json()
    sop = body["sop"]
    got = {q["category"] for q in sop["questions"]}
    for cat in exp.get("question_categories", []):
        if cat not in got:
            problems.append(f"missing question category {cat} (got {sorted(got)})")
    text = sop_text(sop)
    for fact in exp.get("preserved_facts", []):
        if fact not in text:
            problems.append(f"fact not preserved: {fact}")
    hit_values = " | ".join(h["value"] for h in body["report"]["detector_hits"])
    for value in exp.get("forbidden_values", []):
        if value in text and value not in hit_values:
            problems.append(f"invented value not flagged: {value}")
    sent = " ".join(c["user"] + c["system"] for c in fake.calls)
    for bad in exp.get("llm_input_excludes", []):
        if bad in sent:
            problems.append(f"LLM input contains forbidden text: {bad}")
    if exp.get("no_supported_items"):
        items = sop["prerequisites"] + sop["steps"] + sop["warnings"]
        if any(i["status"] == "supported" for i in items):
            problems.append("visual-only items must not be 'supported'")
    return not problems, problems


def run_all() -> list[dict]:
    results = []
    for path in sorted(CASES.glob("*.yaml")):
        ok, problems = run_case(path)
        results.append({"case": path.stem, "passed": ok, "problems": problems})
    return results


if __name__ == "__main__":
    res = run_all()
    for r in res:
        print(("PASS " if r["passed"] else "FAIL ") + r["case"] + "".join(f"\n   - {p}" for p in r["problems"]))
    passed = sum(r["passed"] for r in res)
    print(f"{passed}/{len(res)} eval cases passed")
    sys.exit(0 if passed == len(res) else 1)
