# SOP Builder

A free, stateless website that turns **rough notes, staff interviews and screen recordings** into a
Standard Operating Procedure (prerequisites, numbered steps, warnings, review checklist) and flags the
gaps staff must confirm. No database, no accounts: work in progress stays in your browser.

## How it works
1. **Add sources** – paste/upload notes (.txt .md .docx .pdf), upload an interview transcript
   (.vtt .srt .txt with `Name:` prefixes) or an audio/video recording, or a screen recording.
   Media is decoded in the browser: audio is resampled to 16 kHz mono and sent in ≤100 s WAV chunks
   (Groq Whisper); screen recordings also send one screenshot every 3 s (max 40, near-duplicates skipped)
   in batches of ≤6 to Gemini, which reads the visible text.
2. **Generate** – one LLM call (Gemini, temperature 0, JSON schema) builds the SOP from the redacted,
   escaped source segments. Deterministic code then checks every quote, downgrades weakly supported
   items, flags numbers/amounts/durations/labels that don't appear in the sources, and adds gap questions.
3. **Review** – answer the questions (or mark "Not applicable"), edit step text, and regenerate.
4. **Export** – DOCX or Markdown. If any mandatory question is open, the document is titled
   "DRAFT – NOT VERIFIED". You can also save/load the whole session as JSON.

## Run locally
```bash
pip install -r requirements-dev.txt
# set GEMINI_API_KEY and GROQ_API_KEY in your shell (never in a file in this repo)
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

## Checks
```bash
python scripts/check.py      # ruff + pytest (coverage ≥ 80%) + evals -> reports/TEST_REPORT.md
```
Tests and evals use fake AI clients: no network and no keys needed.

## Privacy
Nothing is stored on the server. PII (emails, phones, cards, Aadhaar, PAN, passwords/tokens) is redacted
before text is sent to an AI provider and in every response. Audio and screenshots are sent to the
Groq and Google Gemini free tiers, so don't upload confidential data.
