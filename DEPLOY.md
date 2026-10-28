# Deploy to Vercel (free plan)

1. Push this repository to GitHub and import it in Vercel (framework is detected as FastAPI;
   entrypoint `app/main.py`, dependencies from `requirements.txt`, Python version from `.python-version`).
2. In **Project → Settings → Environment Variables**, add:

| Name | Required | Default / example |
|---|---|---|
| `GROQ_API_KEY` | **yes** | your Groq key (free at console.groq.com) – used for transcription and the LLM |
| `LLM_MODEL` | recommended | `openai/gpt-oss-120b` (the default) |
| `LLM_FALLBACK_MODEL` | recommended | `openai/gpt-oss-20b` – tried once if the main model returns 429/5xx (same endpoint and key) |
| `LLM_BASE_URL` | no | `https://api.groq.com/openai/v1` |
| `LLM_API_KEY` | no | defaults to `GROQ_API_KEY` |
| `LLM_FALLBACK_BASE_URL` | no | defaults to `LLM_BASE_URL` |
| `LLM_FALLBACK_API_KEY` | no | defaults to `LLM_API_KEY` |
| `GROQ_BASE_URL` | no | `https://api.groq.com/openai/v1` (transcription) |
| `GROQ_WHISPER_MODEL` | no | `whisper-large-v3-turbo` |
| `MAX_REQUEST_BYTES` | no | `4400000` (Vercel body limit is 4.5 MB) |
| `MAX_FILE_BYTES` | no | `4000000` |
| `MAX_CHUNK_SECONDS` | no | `100` |
| `MAX_SOURCE_CHARS` | no | `200000` (hard cap on submitted source text; larger sources are refused) |
| `LLM_TOKEN_BUDGET` | no | `6500` (estimated prompt + max_tokens per LLM request; gpt-oss-120b free tier is 8000 TPM) |
| `LLM_MAX_TOKENS` | no | `2500` (max_tokens for the SOP call) |
| `LLM_REPAIR_MAX_TOKENS` | no | `2000` (max_tokens for the one repair call) |
| `LLM_SOURCE_TOKENS` | no | `3000` (source text per request; above this the most relevant segments are sent) |
| `LLM_SEGMENT_CHARS` | no | `400` (each segment is cut to this length in the prompt) |
| `LLM_MAX_WAIT_S` | no | `20` (on 429, wait and retry once if the provider asks for at most this; else use the fallback model) |
| `RATE_LIMIT_MAX` | no | `60` (requests per window, per IP, per instance) |
| `RATE_LIMIT_WINDOW_S` | no | `600` |

Do **not** set `FAKE_AI` in Vercel (it is a local demo mode with no AI).

3. Deploy. Then open `/api/health`: `ready` should be `true` (it only shows which variables are set,
   never their values).

Screen recordings are read in the visitor's browser with Tesseract.js (loaded from cdn.jsdelivr.net only
when a screen recording is added); no screenshots reach the server or any AI service.

   `public/` is served by Vercel's CDN; the API runs as one function (`vercel.json` sets
   `maxDuration` to 300 s).

Never commit keys or `.env` files. Limits respected: 4.5 MB request/response body, 300 s per request,
read-only disk (nothing is written), no background jobs.
