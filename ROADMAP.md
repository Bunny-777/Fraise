# Fraise — Roadmap

> Voice for anything that speaks _MCP_ — over your data, stored locally and never sold or used for training.

You talk. Fraise routes your intent to the right MCP server, has a back-and-forth if it needs more, and speaks the result back. Adding a capability means adding a server to a config file.

**Status:** ✅ done · 🚧 in progress · ⏳ planned

---

## Architecture

Two layers, and capability lives in only one:

1. **The voice host** — the transport. Mic → STT → LLM → TTS, the orb, and how MCP concepts are rendered as speech. Generic: it never knows what a calendar *is*.
2. **MCP servers** — every capability. Private servers we write; public servers plug in via config.

The voice host only learns to speak MCP better — it never implements a specific task. That belongs in a server.

---

## Phase 0 — Scaffold ✅

- [x] Deepgram Voice Agent loop (STT → LLM → TTS, one WebSocket)
- [x] ElevenLabs WebGL orb mapped to agent state (idle / listening / thinking / speaking)
- [x] `calculate` tool via FastMCP (`@mcp.tool`, pydantic I/O)
- [x] MCP server mounted at `/mcp` for external clients
- [x] LLM-driven tool calling via Deepgram function calling
- [x] AudioWorklet mic pipeline → gapless TTS playback

## Phase 1 — MCP host core ✅

Connect any number of MCP servers from a config file. Route every call to the right one.

- [x] `mcp_servers.json` — servers by name, type (`builtin` / `stdio` / `http`), URL/command, env-var credentials
- [x] `MCPManager` — connects on startup, aggregates tools, routes calls, degrades if one is down
- [x] Tool namespacing — collisions get a server prefix (`slack_search` vs `jira_search`)
- [x] Voice agent reads from `MCPManager`, not the hardcoded calculator
- [x] **Multi-tool chaining** — keeps calling tools until the task is done; step cap of 10 per turn guards against runaway loops.

---

## Phase 2 — Calendar ✅

A private MCP server over your own calendar. Tokens and event data are stored on the backend, never sold or used for training. (Voice + reasoning still transit Deepgram/OpenAI under no-retention API policies until a fully-local mode lands.)

- [x] **Calendar MCP** (`builtin`) — wraps Google Calendar. Tools: `list_events`, `find_free_slot`, `create_event`, `move_event`.
- [x] **Speakable output** — tools return natural English directly; no ISO timestamps or raw IDs reach the LLM.
- [x] **Destructive-action confirmation** — `move_event` returns a spoken confirmation prompt on first call; executes only when called again with `confirmed=True`.
- [x] **OAuth flow** — `/auth/calendar` and `/auth/calendar/callback` handle Google sign-in; token stored locally in `backend/calendar_token.json`.

---

## Phase 3 — Memory & files 🚧

- [x] **Memory MCP** (server) — local SQLite + FTS5 for preferences and things you ask it to remember. Per-user via a stable browser session id (`?sid=`), injected by the host and hidden from the LLM. Tools: `remember`, `recall`, `forget`. (Calendar bias deferred — calendar is off.)
- [x] **File + RAG MCP** (server) — files local, embedded with sqlite-vec. Upload via `POST /upload` (+ sidebar drag-drop); voice tools `ask`, `summarize`, `list_documents`. Hybrid retrieval (dense + BM25, RRF-fused), **late chunking** with a local ONNX long-context encoder (jina-embeddings-v2-small-en, no torch), and a cross-encoder reranker. Generation reuses the voice LLM — tools return passages, the LLM speaks the answer.
- [x] **Conversation continuity** (host + storage) — every user/assistant turn is logged to a `conversation_turns` table (migration 3), keyed on the same session id as memory. On connect, recent turns are folded into the prompt, so a reload, a dropped socket, or a return visit days later doesn't start cold — distinct from the explicit `remember`/`recall` tools, which are for things the user asks Fraise to keep, not the conversation itself.
- [x] **Date/time awareness** (host) — the prompt is given the actual current date and UTC time on every connect, so "today"/"tomorrow"/deadline questions don't get answered from stale training data.
- [ ] **Progress narration** (host) — long-running calls speak MCP progress events.
- [x] **Call recording insights** — `/upload` now takes audio as well as `.txt`/`.md`/`.pdf`. A recording goes to Deepgram's pre-recorded API (`rag/transcribe.py`, reusing the existing `DEEPGRAM_API_KEY` — no new provider), comes back diarized into speaker-labelled lines, and is stored as a document, so it's searchable by the same `ask` as everything else. On top of that, `recording_insights` (`rag/insights.py`) runs the transcript through the same LLM-synthesis pattern `deep_research` uses: what happened, decisions, action items, follow-ups, saved as an artifact the user reads on screen while Fraise speaks the headline. Uploads are tagged `document` or `recording` (migration 7) so "go through my call" finds the right one. Upload-only — you bring an existing recording from your phone, Zoom, Meet, etc. Deliberately not live call-recording: capturing a call as it happens needs a calling-platform integration (Twilio/Zoom/Meet bots) and raises real multi-party consent issues that vary by state and country, so that's out of scope here.
- [x] **Uploads ask before acting** (host) — a new file used to be summarized unprompted. The upload notice now tells the model to offer the choice instead: go through it, or research what it's about — one short spoken question, and it does only the branch you pick (`recording_insights`/`summarize`, or `deep_research` on the subject). The rule lives in the prompt and the injected message, so it costs no new tool and applies to every upload type.

---

## Phase 4 — Polish ✅

- [x] **"What can you do?"** (host) — a per-server capability summary is built at connect time from `MCPManager.functions_by_server()` and folded into the prompt, so Fraise can describe herself in her own words without the host hardcoding descriptions per server — a new server stays describable with zero host-code changes.
- [x] **Barge-in / interruptibility** (host) — a `UserStartedSpeaking` event cuts the agent's audio immediately, and a mute gate now also drops any trailing chunks Deepgram had already generated before it registered the interruption, so talking over Fraise no longer lets her keep speaking over you.
- [x] **Greet once per session** (host) — the frontend marks a `sessionStorage` flag on first connect and sends `?greet=0` on any reconnect within the same tab (reload or an auto-recovered dropped socket); the backend omits the `greeting` field from Deepgram's Settings when asked, so Fraise doesn't re-introduce herself mid-conversation.
- [x] **Dark / light toggle** — theme switch in the header, persisted in `localStorage`. Editorial-wine rebrand: deep burgundy accent, ivory light / warm espresso dark, paired by hue (dark = light mode at night, not an unrelated palette). Fully flat — solid colors only, no gradients or shadows except the orb itself.
- [x] **First-run personalization** — a one-time modal asks your name (stored client-side); the UI greeting and the voice agent's system prompt (`voice_agent.py`) both use it, so Fraise addresses you by name in text and speech.
- [x] **Deploy** — frontend on Vercel (`fraise.vercel.app`), backend on a VM behind nginx + systemd (`deploy/nginx-fraise.conf`, `deploy/fraise.service`). Former Netlify URL now 301-redirects to Vercel via a static `_redirects` rule.

---

## Phase 5 — Tool scale & routing ⏳

A flat function list is fine at ten tools; at a hundred it overflows the model's context and dilutes selection accuracy. This phase keeps routing sharp as the server count grows — without touching the "a capability is one config entry" invariant.

- [ ] **Hierarchical tool selection** — two-stage routing: pick the domain/server first, then the tool, so the LLM never faces the whole flat namespace at once.
- [ ] **Semantic tool retrieval** — embed tool descriptions at connect time and surface only the top-_k_ relevant tools for a given utterance, instead of sending every declaration every turn.
- [ ] **Lazy schema exposure** — hand the model a server's full tool schemas only once its domain is in play, keeping the base prompt small.
- [ ] **Routing eval harness** — a fixed set of `utterance → expected tool` cases so adding a server can't silently regress selection.

---

## Phase 6 — Elicitation as dialogue 🚧

MCP's elicitation spec lets a server pause a call to ask the host for missing information. Rendered as natural spoken back-and-forth, this is Fraise's most conversational moat — the assistant asks a follow-up out loud and resumes the tool once you answer.

- [x] **Every server speaks the whole protocol** (`mcp_manager.py`) — builtin skills used to be plain in-process function calls, which meant anything the spec adds *on top* of a call, elicitation included, was unreachable for them. They now run over an in-memory MCP transport with a real `ClientSession`, so a skill behaves identically whether it lives in this repo or across a socket, and the host declares the elicitation capability to all of them.
- [x] **Elicitation → spoken question** (`host/elicitation.py`) — a server that stops mid-call to ask parks the *suspended coroutine* here; the host hands the model a `needs_input` reply carrying the question and its schema, the model asks it out loud in its own words, and `provide_requested_input` feeds the answer back into that same paused call. The tool never restarts, so everything it had already worked out survives the question. The tool timeout applies only while a tool is working, never while a person is being asked.
- [x] **Answer by voice or by hand** — the same question also appears as a card in the app. Answering there sends the typed values back verbatim, so an address or a menu choice can't be garbled by speech, while the model still drives the conversation. Free-text answers are coerced to the requested type (yes/no, numbers, enum matching); an unclear one leaves the question open and tells the model what's still missing instead of guessing.
- [x] **Multi-turn slot filling** — a server can ask again as many times as it needs; each question is a fresh round trip through the same paused call. `send_email` uses two: the recipient, then the confirmation.
- [x] **Generalized confirmation** (`servers/interaction.py`) — `ask()` and `confirm()` are two lines any server can call, replacing the calendar's bespoke `confirmed=True` second-call trick. Confirmation is now a property of the protocol, not per-server code, and a host without elicitation degrades to "not confirmed" rather than failing.
- [x] **Email by voice** (`servers/email.py`) — the first skill built on it, and the reason it matters: dictate an email and it asks who it's for, reads the draft back, and sends only on a clear yes. Wraps the existing SMTP sender, so it needs no new credentials.
- [ ] **Progress narration** (carried from Phase 3) — long-running calls speak MCP progress events so silence never reads as a hang.
- [ ] **URL-mode elicitation** — the spec's out-of-band path (credentials, OAuth) is declined for now; folding it in would replace calendar's bespoke `auth_redirect` action with the standard flow.

---

## Phase 7 — Ambient & proactive ⏳

Today Fraise only speaks when spoken to. Ambient use is a distinct moat: an assistant that initiates.

- [ ] **Wake word / always-listening** — hands-free activation without holding the orb.
- [ ] **Proactive nudges** — Fraise initiates from events ("your 3pm is in ten minutes") rather than only reacting.
- [ ] **Reminders & scheduling server** — a timer/cron MCP server for "remind me in an hour."
- [ ] **Telephony bridge** — a Twilio/SIP inbound path so Fraise is reachable by phone call, not just the browser.
- [ ] **Background session & push** — keep a session warm and deliver notifications when the tab is closed.

---

## Phase 8 — Developer platform ⏳

Turn "edit a JSON file" into a real ecosystem — the long-term wedge.

- [ ] **Server registry** — browse and one-click enable community MCP servers instead of hand-editing `mcp_servers.json`.
- [ ] **`create-fraise-server` scaffold** — an SDK + template that bakes in the speakable-output contract so a new server sounds right by default.
- [ ] **Settings UI** — connect, authenticate, and toggle servers from the app.
- [ ] **Generic OAuth broker** — one reusable auth flow any server can lean on, instead of calendar's bespoke `/auth/calendar` routes.
- [ ] **Observability** — per-tool call logs, latency, and error rates surfaced in-app.

---

## Phase 9 — Fully-local & privacy mode ⏳

Deliver on the privacy stance end to end: with local models on, nothing leaves the machine.

- [ ] **Local STT** — on-device speech recognition (whisper.cpp or equivalent) behind the same transport.
- [ ] **Local LLM** — a local model (llama.cpp / Ollama) driving tool calls through the existing function-calling interface.
- [ ] **Local TTS** — on-device voice (Piper / Kokoro) so the speech loop needs no cloud.
- [ ] **Offline degradation** — network-dependent servers fail gracefully while on-device ones (memory, RAG, filesystem, calculator) keep working.
- [ ] **Encryption at rest** — encrypt the SQLite store holding memory and documents.

---

## Phase 10 — Accounts & multi-device ⏳

- [ ] **Real accounts** — replace the anonymous browser session id with sign-in and sync memory/documents across devices.
- [ ] **Mobile** — a PWA/React Native client so Fraise travels off the desktop.
- [ ] **Shared spaces** — opt-in team memory and documents, with per-user isolation preserved by default.

---

## Phase 11 — Profiles & personas 🚧

Multiple named assistants in one browser — a row of avatars you tap to switch (like Strawberry's companion switcher), each with its own avatar, name, instructions, and its own memory. Switch to "Work" and Fraise carries your work facts, docs, and custom instructions; switch to "Personal" and it's a clean, separate mind. Every assistant shares the same config *shape* — only the values differ. This is not new capability: it reuses the per-session scoping that memory, RAG, and conversation history already key on, so isolation comes for free. Assistants live in the **voice host** as a scope key + a prompt fragment; no MCP server changes, and the "a capability is one config entry" invariant holds.

- [x] **Assistant as a scope key** — each assistant carries a stable id used exactly where `session_id` is today (memory rows, RAG documents, `conversation_turns`), so it accumulates its own remembered facts, uploaded files, and conversation continuity in isolation, with zero server-side changes. The list lives in `localStorage` (`assistants.ts`); a first-run migration seeds a default assistant whose id reuses the old `fraise_sid`, so a returning user's memory and documents carry over instead of orphaning.
- [x] **Per-assistant config** — a settings panel per assistant: avatar, name, and custom instructions (its system-prompt fragment: tone, role, standing rules). Instructions and the persona name fold into the prompt (`persona`/`instructions` query params → `_build_settings`) the same way `user_name` and date/capabilities already do; a non-default name renames the assistant in-prompt and in its greeting. (A view of *its* Memory and Transcripts is deferred — it needs new backend read endpoints.)
- [x] **Avatar switcher (frontend)** — a persistent top-bar pill of assistant avatars with a `+` to create one, plus create/rename/edit/delete (localStorage-backed, like the first-run name modal). Tapping another avatar reconnects `/ws` with that assistant's id + instructions and clears the transcript, giving a clean persona swap mid-app with no reload; tapping the active one opens its editor.
- [x] **Voice-native switching** — "switch to my work assistant" as a spoken command. A tiny `personas` MCP server exposes `switch_assistant(name)`, which returns a fire-and-forget `_action` the host forwards to the browser; the browser matches the name against its local list and reconnects as that persona. The other assistants' names are folded into the prompt so the model knows it can switch and what to pass.
- [x] **Cross-assistant isolation guarantee** — one assistant's memory or documents never surface in another; because the id *is* the `session_id` scope, isolation is the storage default, and a shared/global scope stays opt-in (unbuilt), not default.

> Distinct from Phase 10 accounts: assistants are multiple personas for one person on one device; accounts are one identity synced across devices. They compose — an account can own many assistants — but assistants ship first and need no auth.

---

## Public MCP servers

Supported with no new code — the host already speaks `http` and `stdio`, so connecting one is a single entry in `mcp_servers.json`. Live so far:

- [x] **Weather** (builtin) — Open-Meteo geocode + current conditions, zero-config (no API key), one plain-English sentence out.
- [x] **Filesystem** (`stdio`, `@modelcontextprotocol/server-filesystem`) — scoped to one dedicated folder, not the whole machine, since this is voice + LLM-driven access.
- [x] **Web search** (`stdio`, `tavily-mcp`) — needs `TAVILY_API_KEY`. Brave Search was tried first; its official package is deprecated ("no longer supported" on npm), hence Tavily.
- [x] **`${VAR}` credential interpolation** (`mcp_manager.py`) — `mcp_servers.json` is git-tracked, so secrets are written as `${TAVILY_API_KEY}` and resolved from `.env` at connect time, never committed in literal.

Planned integrations, added as the need arises rather than up front:

- [ ] **Slack** — read/post messages, summarize channels by voice.
- [ ] **GitHub / Jira / Linear** — issues, PRs, and status by voice.
- [ ] **Notion / Google Docs** — read and append to notes.
- [ ] **Gmail — compose & send by voice** — SMTP sending now ships as the `email` server (Phase 6), which drafts, asks who it's for, reads the message back, and sends only on a clear yes. Gmail proper would mirror the Calendar OAuth pattern (`google-auth-oauthlib` + `googleapiclient`, file-based token) with the send scope, which buys threading and triage — reading and replying to mail, not just sending it — and would let `daily.py`'s `email` lane hand its drafts straight to a real outbox.
- [ ] **Zapier / Make** — reach thousands of long-tail apps through one MCP bridge.

---

## Adding a capability

**Private server:**
1. Write `backend/app/mcp_<name>.py` — `@mcp.tool` functions with pydantic types.
2. Add it to `mcp_servers.json` as `"type": "builtin"`.
3. Voice-callable on next start. The speakable-output layer picks it up automatically.

**External server:** add one `http` / `stdio` entry to `mcp_servers.json`. Restart.

The voice transport never changes. That is the point.
