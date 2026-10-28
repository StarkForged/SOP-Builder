# Deploy to Vercel (free plan)

1. Push this repository to GitHub and import it in Vercel (framework is detected as FastAPI;
   entrypoint `app/main.py`, dependencies from `requirements.txt`, Python version from `.python-version`).
2. In **Project → Settings → Environment Variables**, add:

| Name | Required | Default / example |
|---|---|---|
| `GEMINI_API_KEY` | yes | your Google AI Studio key |
| `GROQ_API_KEY` | yes | your Groq key |
| `LLM_BASE_URL` | no | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `LLM_MODEL` | no | `gemini-2.5-flash` |
| `VISION_MODEL` | no | `gemini-2.5-flash` |
| `GROQ_BASE_URL` | no | `https://api.groq.com/openai/v1` |
| `GROQ_WHISPER_MODEL` | no | `whisper-large-v3-turbo` |
| `MAX_REQUEST_BYTES` | no | `4400000` (Vercel body limit is 4.5 MB) |
| `MAX_FILE_BYTES` | no | `4000000` |
| `MAX_FRAMES` | no | `6` |
| `MAX_FRAMES_BYTES` | no | `3500000` |
| `MAX_CHUNK_SECONDS` | no | `100` |
| `RATE_LIMIT_MAX` | no | `60` (requests per window, per IP, per instance) |
| `RATE_LIMIT_WINDOW_S` | no | `600` |

3. Deploy. `public/` is served by Vercel's CDN; the API runs as one function (`vercel.json` sets
   `maxDuration` to 300 s).

Never commit keys or `.env` files. Limits respected: 4.5 MB request/response body, 300 s per request,
read-only disk (nothing is written), no background jobs.
