import asyncio
import logging

from app.servers.research import llm

from . import store

logger = logging.getLogger(__name__)

EVERY_N_TURNS = 12
_RECENT_TURNS = 16
_MAX_NEW_FACTS = 3

_running: set[asyncio.Task] = set()

_SYSTEM = (
    "You read a short stretch of conversation and pick out only what is worth "
    "remembering about this person for months to come.\n\n"
    "Keep: stable preferences, how they like things done, their role, the people "
    "and projects that recur in their life, constraints that will still hold next "
    "month.\n"
    "Discard: anything about the current task, questions they asked, what the "
    "assistant said, one-off facts, dates and times, small talk, and anything you "
    "are inferring rather than being told. When in doubt, discard — a wrong "
    "memory is worse than a missing one.\n\n"
    'Return JSON: {"facts": [...]}, each a single short sentence written about '
    "them in the third person, e.g. \"Prefers window seats on flights.\" Return an "
    "empty list if nothing qualifies, which is the common case."
)

def _is_new(candidate: str, existing: list[str]) -> bool:
    words = {w for w in candidate.lower().split() if len(w) > 3}
    if not words:
        return False
    for fact in existing:
        other = {w for w in fact.lower().split() if len(w) > 3}
        if other and len(words & other) / len(words | other) > 0.5:
            return False
    return True

async def _consolidate(session_id: str) -> list[str]:
    turns = await asyncio.to_thread(store.recent_turns, session_id, _RECENT_TURNS)
    if len(turns) < 4:
        return []

    transcript = "\n".join(
        f"{'User' if role == 'user' else 'Assistant'}: {content}" for role, content in turns
    )
    try:
        data = await llm.complete_json(_SYSTEM, transcript, model=llm.SYNTH_MODEL, max_tokens=400)
    except llm.LLMUnavailable as exc:
        logger.info("memory consolidation unavailable: %s", exc)
        return []

    raw = data.get("facts")
    if not isinstance(raw, list):
        return []

    existing = await asyncio.to_thread(store.recall, session_id, "", 60)
    saved: list[str] = []
    for item in raw[:_MAX_NEW_FACTS]:
        fact = llm.strip_markdown(str(item or "")).strip()
        if not fact or len(fact) > 200:
            continue
        if not _is_new(fact, existing + saved):
            continue
        await asyncio.to_thread(store.remember, session_id, fact)
        saved.append(fact)

    if saved:
        logger.info("consolidated %d fact(s) for %s", len(saved), session_id[:8])
    return saved

def maybe_consolidate(session_id: str, turn_count: int) -> None:
    if not session_id or turn_count <= 0 or turn_count % EVERY_N_TURNS:
        return
    task = asyncio.create_task(_consolidate(session_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
