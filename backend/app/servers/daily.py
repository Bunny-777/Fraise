import asyncio
import contextlib
import json
import logging
import re
import time
from datetime import datetime, timezone

from app.host import agent_loop, bus
from app.servers.memory import store as memory_store
from app.servers.research import llm
from app.storage import db

logger = logging.getLogger(__name__)

_LANE_LIMIT = 2
_TASK_BUDGET = 420

_CHUNK_CHARS = 4000
_MAX_TASKS_PER_CHUNK = 16
_MAX_TASKS_TOTAL = 48
_DETAIL_MAX_CHARS = 800
_SEGMENT_MAX_TOKENS = 4096

_running: set[asyncio.Task] = set()

# Only a display glyph — what a task actually needed is decided by the model at
# run time, not by sorting it into a lane up front.
_LANE_BY_TOOL = {
    "remember": "remember",
    "recall": "remember",
    "forget": "remember",
    "deep_research": "research",
    "get_weather": "answer",
    "switch_assistant": "note",
}
_LANE_BY_PREFIX = (
    ("tavily", "research"),
    ("search", "research"),
    ("send_email", "email"),
    ("ask", "note"),
    ("summarize", "note"),
    ("recording", "note"),
    ("list_", "note"),
    ("calculate", "answer"),
    ("read_", "note"),
    ("write_", "note"),
)

def _lane_for(tools: list[str]) -> str:
    for tool in tools:
        if tool in _LANE_BY_TOOL:
            return _LANE_BY_TOOL[tool]
        for prefix, lane in _LANE_BY_PREFIX:
            if tool.startswith(prefix):
                return lane
    return "answer" if not tools else "note"

_SEGMENT_SYSTEM = (
    "You turn a spoken brain-dump of someone's day into a list of separate, atomic "
    "tasks. You may be shown only one part of a longer dictation — segment just "
    "what's given here; don't invent or assume content from parts you can't see.\n\n"
    "Return JSON: {\"tasks\": [...]}. Each task is:\n"
    '  "title": an imperative phrase, max 8 words, naming the one thing to do.\n'
    '  "detail": everything needed to carry it out on its own, without the rest of '
    "the dictation for context — the subject, the person, the time, the amount. Keep "
    "the user's own words where they matter.\n\n"
    "Rules:\n"
    "- SPLIT compound speech into separate tasks. \"Email Sam and book a table\" is two.\n"
    "- Do not invent tasks the person did not say. Do not merge unrelated ones.\n"
    "- Do not decide how a task should be carried out — that is worked out later. "
    "Just capture what was asked for, completely.\n"
    "- Keep the tasks in the order they were spoken."
)

def _today_line(tz_offset_min: int) -> str:
    now = datetime.now(timezone.utc)
    local = now.timestamp() - tz_offset_min * 60
    d = datetime.fromtimestamp(local, tz=timezone.utc)
    return d.strftime("Today is %A, %B %-d, %Y, local time %-I:%M %p.")

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_TASK_OBJ = re.compile(r'\{[^{}]*"title"[^{}]*\}', re.S)

def _split_into_chunks(text: str, max_chars: int = _CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for sent in _SENT_SPLIT.split(text):
        if current and len(current) + len(sent) + 1 > max_chars:
            chunks.append(current.strip())
            current = sent
        else:
            current = f"{current} {sent}".strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]

def _context_block(session_id: str) -> str:
    facts = memory_store.recall(session_id, limit=8)
    turns = memory_store.recent_turns(session_id, limit=6)
    parts = []
    if facts:
        parts.append(
            "Known facts about this person — use them only to resolve references, "
            "never to invent new tasks:\n" + "\n".join(f"- {f}" for f in facts)
        )
    if turns:
        recent = "\n".join(f"{role}: {content}" for role, content in turns)
        parts.append(f"Recent conversation, for resolving pronouns/references only:\n{recent}")
    return "\n\n".join(parts)

def _parse_segment_response(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        return [t for t in data["tasks"] if isinstance(t, dict)]

    tasks = []
    for m in _TASK_OBJ.finditer(raw):
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            tasks.append(d)
    return tasks

async def _segment_chunk(text: str, tz_offset_min: int, context: str) -> list[dict]:
    parts = [_today_line(tz_offset_min)]
    if context:
        parts.append(context)
    parts.append(f"The day, as spoken:\n{text.strip()}")
    ask = "\n\n".join(parts)

    try:
        raw = await llm.complete(
            _SEGMENT_SYSTEM, ask, json_mode=True, model=llm.SYNTH_MODEL,
            max_tokens=_SEGMENT_MAX_TOKENS,
        )
    except llm.LLMUnavailable as exc:
        logger.warning("day segmentation unavailable (%s); one-task fallback", exc)
        return [_fallback_task(text)]

    raw_tasks = _parse_segment_response(raw)
    if not raw_tasks:
        return [_fallback_task(text)]

    tasks: list[dict] = []
    for t in raw_tasks[:_MAX_TASKS_PER_CHUNK]:
        title = llm.strip_markdown(str(t.get("title") or "")).strip()[:80]
        detail = str(t.get("detail") or "").strip()[:_DETAIL_MAX_CHARS]
        if not title and not detail:
            continue
        tasks.append({"title": title or detail[:60], "lane": "note", "detail": detail or title})
    return tasks or [_fallback_task(text)]

async def _segment(text: str, tz_offset_min: int, session_id: str) -> list[dict]:
    context = await asyncio.to_thread(_context_block, session_id)
    chunks = _split_into_chunks(text)

    if len(chunks) == 1:
        merged = await _segment_chunk(chunks[0], tz_offset_min, context)
    else:
        sem = asyncio.Semaphore(_LANE_LIMIT)

        async def guarded(c: str) -> list[dict]:
            async with sem:
                return await _segment_chunk(c, tz_offset_min, context)

        results = await asyncio.gather(*(guarded(c) for c in chunks))
        merged = [t for chunk_tasks in results for t in chunk_tasks]

    merged = merged[:_MAX_TASKS_TOTAL] or [_fallback_task(text)]
    return [{**t, "id": f"t{i}-{db.new_id()[:6]}"} for i, t in enumerate(merged)]

def _fallback_task(text: str) -> dict:
    return {"title": text.strip()[:60] or "Your note", "lane": "note", "detail": text.strip()}

_TOOL_NOTE = {
    "remember": "Saving it to memory…",
    "deep_research": "Putting agents on it…",
    "get_weather": "Checking the weather…",
    "calculate": "Working out the numbers…",
}

def _note_for(tool: str) -> str:
    if tool in _TOOL_NOTE:
        return _TOOL_NOTE[tool]
    if tool.startswith(("tavily", "search")):
        return "Searching the web…"
    return f"Using {tool.replace('_', ' ')}…"

async def _run_task(task: dict, day_id: str, session_id: str) -> dict:
    tid, detail = task["id"], task["detail"]
    started = time.monotonic()

    def emit(status: str, note: str, **extra) -> None:
        bus.publish(session_id, {
            "type": "day_task", "day_id": day_id, "id": tid,
            "status": status, "note": note, **extra,
        })

    emit("running", "Working out what this needs…")

    try:
        objective = f"{task['title']}\n\n{detail}" if task.get("title") else detail
        outcome = await asyncio.wait_for(
            agent_loop.run(
                objective, session_id, on_step=lambda tool: emit("running", _note_for(tool))
            ),
            _TASK_BUDGET,
        )
        used = outcome["tools"]
        result = outcome["text"] or "Handled."
        lane = _lane_for(used)
        note = (
            "Handled with " + ", ".join(dict.fromkeys(t.replace("_", " ") for t in used)) + "."
            if used else "Answered."
        )
        elapsed = round(time.monotonic() - started, 1)
        emit("done", note, result=result, sources=[], elapsed=elapsed, lane=lane)
        return {
            **task, "lane": lane, "status": "done", "note": note,
            "result": result, "sources": [], "elapsed": elapsed, "tools": used,
        }

    except Exception as exc:
        logger.warning("day task %s failed: %r", tid, exc)
        reason = llm.friendly_error(exc)
        emit("failed", "Couldn't finish this one.", error=reason)
        return {**task, "status": "failed", "error": reason}

async def _run(day_id: str, text: str, session_id: str, tz_offset_min: int) -> None:
    state: dict[str, dict] = {}
    try:
        bus.publish(session_id, {
            "type": "day", "day_id": day_id, "status": "segmenting",
            "note": "Splitting your day into tasks…",
        })
        tasks = await _segment(text, tz_offset_min, session_id)
        state.update({t["id"]: {**t, "status": "queued"} for t in tasks})
        state_lock = asyncio.Lock()

        def save(status: str, spoken: str | None = None, error: str | None = None) -> None:
            db.save_day(day_id, session_id, text, status, list(state.values()), spoken, error)

        bus.publish(session_id, {
            "type": "day", "day_id": day_id, "status": "running", "text": text,
            "tasks": list(state.values()),
        })
        await asyncio.to_thread(save, "running")

        sem = asyncio.Semaphore(_LANE_LIMIT)

        async def guarded(t: dict) -> dict:
            async with sem:
                finished = await _run_task(t, day_id, session_id)
            async with state_lock:
                state[t["id"]] = finished
                await asyncio.to_thread(save, "running")
            return finished

        done = await asyncio.gather(*(guarded(t) for t in tasks))

        n = len(done)
        handled = sum(1 for d in done if d.get("status") in ("done", "proposed"))
        spoken = (
            f"That's your day sorted — {handled} of {n} handled. "
            "The research is written up and any drafts are ready for you to check."
        )
        await asyncio.to_thread(save, "done", spoken)
        bus.publish(session_id, {
            "type": "day", "day_id": day_id, "status": "done", "spoken": spoken,
        })
    except Exception as exc:
        logger.exception("day run %s crashed", day_id)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                db.save_day, day_id, session_id, text, "failed",
                list(state.values()), None, str(exc)[:160],
            )
        bus.publish(session_id, {
            "type": "day", "day_id": day_id, "status": "failed", "error": str(exc)[:160],
        })

def start(text: str, session_id: str, tz_offset_min: int = 0) -> str:
    day_id = db.new_id()
    task = asyncio.create_task(_run(day_id, text, session_id, tz_offset_min))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return day_id
