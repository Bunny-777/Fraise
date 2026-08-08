import json
import logging
import time
from datetime import datetime, timezone

from app.host.mcp_manager import manager
from app.servers.research import llm

logger = logging.getLogger(__name__)

_TTL = 180
_MAX = 4
_cache: dict[str, tuple[float, list[dict]]] = {}

RESTING = [
    {"icon": "search", "label": "Research", "text": "Research the best SDE internships and make me a deck"},
    {"icon": "web", "label": "Compare", "text": "Compare the best electric cars under 20 lakhs"},
    {"icon": "remember", "label": "Memory", "text": "Remember I prefer window seats"},
    {"icon": "math", "label": "Quick math", "text": "What's 18% of 240?"},
]

_ICONS = ("search", "web", "remember", "recall", "doc", "math")

_SYSTEM = (
    "You write the few things a voice assistant offers on its home screen, for one "
    "specific person, based on what it already knows about them right now.\n\n"
    f'Return JSON: {{"suggestions": [...]}} with exactly {_MAX} items. Each is:\n'
    '  "text": what the person would say out loud, in their words, 4-10 words. It '
    "must be something this assistant can actually do with the skills listed below.\n"
    f'  "label": one or two words naming it, e.g. "Debrief" or "Research".\n'
    f'  "icon": one of {", ".join(_ICONS)}.\n\n'
    "Rules:\n"
    "- Lead with what is specific to this person: their own files, recordings, and "
    "known preferences by name. A suggestion that would fit any user is a wasted slot.\n"
    "- Every suggestion must be doable with the listed skills and nothing else. If no "
    "skill books, buys, orders, calls, or sends something, never suggest it — a "
    "preference like liking window seats is a thing to know about them, not a thing "
    "to act on. Ask yourself which listed skill would run, and drop it if none would.\n"
    "- Never invent a document, recording, person, or fact not given to you.\n"
    "- Vary the kinds of thing offered; don't give four versions of one idea.\n"
    "- Plain text, no markdown, no quotes around the text."
)

def _time_of_day() -> str:
    hour = datetime.now(timezone.utc).hour
    if hour < 4:
        return "It is late at night for them."
    if hour < 12:
        return "It is morning for them."
    if hour < 17:
        return "It is the afternoon for them."
    return "It is the evening for them."

def _clean(raw) -> list[dict]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = llm.strip_markdown(str(item.get("text") or "")).strip().strip('"')
        label = llm.strip_markdown(str(item.get("label") or "")).strip()[:14]
        icon = str(item.get("icon") or "").strip().lower()
        if not text or len(text) > 90:
            continue
        out.append({
            "icon": icon if icon in _ICONS else "search",
            "label": label or "Try",
            "text": text,
        })
    return out[:_MAX]

async def for_session(session_id: str) -> list[dict]:
    if not session_id:
        return RESTING

    hit = _cache.get(session_id)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]

    briefings = await manager.briefings(session_id)
    if not briefings:
        return RESTING

    skills = "\n".join(
        f"- {name}: {', '.join(t['name'] for t in tools)}"
        for name, tools in manager.functions_by_server().items()
    )
    ask = (
        f"{_time_of_day()}\n\nWhat the assistant knows about them:\n"
        + "\n\n".join(briefings.values())
        + f"\n\nSkills it can actually use:\n{skills}"
    )

    try:
        data = await llm.complete_json(_SYSTEM, ask, model=llm.SYNTH_MODEL, max_tokens=500)
    except llm.LLMUnavailable as exc:
        logger.info("suggestions unavailable: %s", exc)
        return RESTING

    suggestions = _clean(data.get("suggestions"))
    if len(suggestions) < 2:
        return RESTING

    _cache[session_id] = (time.monotonic(), suggestions)
    return suggestions

def invalidate(session_id: str) -> None:
    _cache.pop(session_id, None)
