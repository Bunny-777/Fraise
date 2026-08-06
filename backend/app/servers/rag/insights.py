import asyncio
import json
import logging
import time
from pathlib import Path

from app.host import bus
from app.servers.research import llm
from app.storage import db

logger = logging.getLogger(__name__)

TRANSCRIPT_BUDGET = 60_000

_AGENT = "recording"
_LABEL = "Recording"

_SECTIONS = (
    ("summary", "What happened"),
    ("decisions", "Decisions"),
    ("action_items", "Action items"),
    ("follow_ups", "Follow-ups"),
)

_SYSTEM = (
    "You are given the transcript of a recorded call or meeting, with each speaker "
    "labelled. Turn it into a debrief for someone who wasn't there.\n"
    "Rules: use ONLY what the transcript says — never invent a name, number, date, "
    "or commitment. Attribute an action item to a speaker only when the transcript "
    "makes it clear whose it is. Speaker labels are positions, not names: use a "
    "person's name only if it is said out loud. If a section has nothing in it, "
    "return an empty array rather than padding it.\n\n"
    "Return JSON with exactly these keys, in this order:\n"
    '  "title": a short headline naming what the call was about (max 8 words)\n'
    '  "summary": 3-6 strings, each one sentence, covering what was discussed\n'
    '  "decisions": what was actually settled, one string each\n'
    '  "action_items": what someone committed to do, one string each, naming who '
    "and by when if it was said\n"
    '  "follow_ups": open questions and anything left unresolved, one string each\n'
    '  "spoken": TWO sentences a voice assistant reads aloud — what the call was '
    "about, and the single most important decision or outcome. Write it last, and "
    "make it agree with the lists above: never state a count you haven't counted "
    "there. Natural English, no lists, no markdown.\n\n"
    + llm.PLAIN_TEXT_RULE
)

class InsightsFailed(RuntimeError):
    pass

def _lines(raw) -> list[str]:
    items = raw.splitlines() if isinstance(raw, str) else raw
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not item:
            continue
        text = " — ".join(str(v) for v in item.values()) if isinstance(item, dict) else str(item)
        if line := llm.strip_markdown(text):
            out.append(line)
    return out

async def debrief(session_id: str, filename: str, transcript: str) -> dict:
    run_id = db.new_id()
    stem = Path(filename).stem or "the recording"
    query = f"Debrief of {stem}"
    started = time.monotonic()

    def run_event(status: str, **extra) -> None:
        bus.publish(session_id, {
            "type": "run", "run_id": run_id, "status": status, "query": query,
            "format": "doc", **extra,
        })

    def step(status: str, note: str, **extra) -> None:
        bus.publish(session_id, {
            "type": "agent", "run_id": run_id, "agent": _AGENT, "label": _LABEL,
            "status": status, "note": note, **extra,
        })

    run_event("planning", agents=[], note="Opening the recording")
    run_event("running", agents=[
        {"agent": _AGENT, "label": _LABEL, "status": "reading",
         "note": f"Reading {len(transcript):,} characters of transcript"}
    ])
    step("thinking", "Pulling out the decisions, action items, and follow-ups")

    try:
        data = await llm.complete_json(
            _SYSTEM,
            f"Recording: {stem}\n\nTranscript:\n{transcript[:TRANSCRIPT_BUDGET]}",
            model=llm.SYNTH_MODEL,
            max_tokens=1400,
        )
    except llm.LLMUnavailable as exc:
        logger.warning("recording debrief failed: %s", exc)
        step("failed", "Couldn't finish", error=str(exc)[:160])
        run_event("failed", error=str(exc)[:160])
        raise InsightsFailed("I couldn't reach the model that writes up recordings.") from exc

    sections = [
        {"heading": heading, "bullets": _lines(data.get(key))}
        for key, heading in _SECTIONS
    ]
    sections = [s for s in sections if s["bullets"]]
    if not sections:
        step("failed", "Nothing usable in that transcript")
        run_event("failed", error="Nothing usable came back from the transcript.")
        raise InsightsFailed("I couldn't make anything useful out of that recording.")

    title = llm.strip_markdown(str(data.get("title") or "")) or f"Debrief of {stem}"
    spoken = " ".join(llm.strip_markdown(str(data.get("spoken") or "")).split()) or (
        "I've written the call up — it's on your screen."
    )
    body = {
        "title": title,
        "query": query,
        "format": "doc",
        "sections": sections,
        "citations": [],
        "agents": [{"agent": _AGENT, "label": _LABEL, "ok": True}],
    }
    await asyncio.to_thread(db.save_artifact, run_id, session_id, title, "doc", json.dumps(body))

    elapsed = round(time.monotonic() - started, 1)
    highlights = [b for s in sections for b in s["bullets"]][:8]
    step("done", f"Done in {elapsed}s", summary="\n".join(highlights), elapsed=elapsed)
    run_event("done", artifact=body, spoken=spoken)

    by_heading = {s["heading"]: s["bullets"] for s in sections}
    return {
        "title": title,
        "spoken": spoken,
        "decisions": by_heading.get("Decisions", []),
        "action_items": by_heading.get("Action items", []),
        "follow_ups": by_heading.get("Follow-ups", []),
    }
