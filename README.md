# Fraise 🍓

A voice-first AI assistant you talk to out loud. Say what you need — a calendar event, a question, a whole day's worth of tasks, a document to search — and it does it.

**Live:** [fraise.vercel.app](https://fraise.vercel.app)

## Features

- Real-time voice over a single WebSocket (Deepgram Voice Agent: STT + LLM + TTS in one stream).
- Skills are MCP servers — calculator, calendar, memory, weather, web research, on-device document Q&A.
- Dictate your whole day and it gets split into tasks, fanned out to parallel agents, and handled.
- Dictation mode — speak long messages or essays instead of typing. Two models run at once, fully on-device: a tiny model streams provisional captions ~5x/sec as you talk (words appear while you're still speaking, not after), while a small, fast model (`base` by default) confirms each utterance the moment you pause — Whisper's encoder cost is fixed per utterance regardless of length, so bigger models like `large-v3-turbo` mean multi-second lag no matter how tightly VAD is tuned; `base` keeps confirmation under ~1s. Nothing leaves your machine. Voice commands ("comma", "new paragraph", "question mark", …) become real punctuation instantly, no LLM round trip. On "Finish", an LLM pass polishes capitalization/paragraphing and detects if you were dictating an email — pulling out a subject and body, and a recipient address only if you actually said one out loud. Every dictation is saved and reachable anytime from the sidebar. Set `STT_MODEL` (default `base`) for more accuracy at the cost of latency, and `STT_LIVE_MODEL` (default `tiny`) for the live captions.
- Dictate an email and send it — Fraise drafts the subject/body from what you said, you confirm the recipient, and it sends over SMTP (see Getting started).
- Multi-agent research: ask a question, a team of agents searches different sources in parallel and writes up a doc or deck.
- On-device document Q&A — upload a PDF/txt/md and ask about it; nothing leaves your machine.
- Persistent memory across a session, and spoken confirmation before anything destructive.

## How document search works

Upload triggers **late chunking**: the whole document is embedded first, then split, so each chunk's vector keeps the context of the passage around it. A question runs dense (vector) and keyword (BM25) search in parallel, fuses the results, and a cross-encoder reranks the top candidates before they reach the voice model. All local — no PyTorch, no embedding API.

## Tech stack

- **Frontend:** React 19, TypeScript, Vite, React Three Fiber (the orb).
- **Backend:** Python, FastAPI, WebSockets, the MCP Python SDK.
- **Voice:** Deepgram Voice Agent.
- **Retrieval:** ONNX Runtime + local embeddings, sqlite-vec, SQLite FTS5, a cross-encoder reranker — no PyTorch, no embedding API.
- **Storage:** SQLite.

## Getting started

Prerequisites: Python 3.11+, Node 18+, a Deepgram API key.

```bash
# .env at the repo root: DEEPGRAM_API_KEY, GROQ_API_KEY, TAVILY_API_KEY
#
# Optional, to send emails dictated in Dictation mode — a Gmail app password
# works (myaccount.google.com/apppasswords), or any SMTP provider:
#   SMTP_USER, SMTP_PASSWORD, and optionally SMTP_HOST/SMTP_PORT/SMTP_FROM.
# Without these, dictated emails still draft — sending returns a clear error.

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main          # http://localhost:8000

cd frontend
npm install
npm run dev                 # http://localhost:5173
```

Open the frontend, allow microphone access, and start talking.

## Project layout

```
backend/
  app/
    main.py            # FastAPI app: WebSocket, upload, dictate, research routes
    host/               # voice session + MCP tool router
    servers/            # one folder/file per MCP skill
  mcp_servers.json      # one entry per skill; edit this to add tools
frontend/
  src/
    useVoiceAgent.ts    # WebSocket + audio streaming hook
    Hero.tsx            # landing page
    App.tsx             # workspace
```

## Adding a skill

Write an MCP server (see `backend/app/servers/calculator.py`), add one entry to `mcp_servers.json`, restart. No changes to the agent itself.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Liveness check. |
| `WS` | `/ws?sid=<id>` | Voice session. |
| `WS` | `/ws/dictation?sid=<id>` | Dictation mode — stream PCM16 in, punctuated transcript segments out, then a polished + email-detected final. |
| `GET` | `/dictations?sid=<id>` | List saved dictations. |
| `GET` | `/dictations/{id}?sid=<id>` | Fetch one dictation — raw text, polished text, email fields. |
| `POST` | `/dictations/{id}/polish?sid=<id>` | Re-run the LLM cleanup/email-detection pass. |
| `POST` | `/dictations/{id}/send?sid=<id>` | Send a dictated email over SMTP. |
| `POST` | `/upload?sid=<id>` | Add a document. |
| `POST` | `/dictate?sid=<id>` | Segment a day's dictation into tasks. |
| `GET` | `/days?sid=<id>` | List past day runs. |
| `GET` | `/days/{id}?sid=<id>` | Reopen a past day run — full task results. |
| `GET` | `/agents/stream?sid=<id>` | SSE progress for research/dictate runs. |
| `GET` | `/auth/calendar` | Google Calendar OAuth. |

See [ROADMAP.md](ROADMAP.md) for what's next.
