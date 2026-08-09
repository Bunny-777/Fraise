import asyncio
import json
import logging
from typing import Callable

from app.host import tool_index
from app.host.mcp_manager import manager
from app.servers.research import llm

logger = logging.getLogger(__name__)

MAX_STEPS = 4
# Must exceed the LLM client's own rate-limit backoff, or a retry that is working
# gets cancelled out from under it.
_STEP_TIMEOUT = 150

_SYSTEM = (
    "You are handling one task from someone's day, on their behalf and without "
    "them watching. Decide for yourself what the task needs and use the tools "
    "available to you — one, several in sequence, or none at all if you can "
    "simply answer it.\n\n"
    "Rules:\n"
    "- Reach for a tool whenever it would make the answer real rather than "
    "guessed. Never invent a fact, a price, a listing, or a result.\n"
    "- If a tool asks you for more information, you cannot ask the person — they "
    "aren't here. Do the most sensible thing with what you have, or report what "
    "is missing.\n"
    "- When you have what you need, reply with 2-3 tight sentences telling them "
    "what you did and what came of it. Plain text only: no markdown, no "
    "asterisks, no bullet points, no URLs.\n"
    "- If you genuinely cannot do the task, say so plainly in one sentence."
)

TOOLS_PER_STEP = 8

def _trim(functions: list[dict]) -> list[dict]:
    """Only the first line of each description — the full text costs tokens the
    per-minute budget can't spare once every tool is declared on every step."""
    return [
        {
            "name": f["name"],
            "description": (f["description"] or "").split("\n")[0][:140],
            "parameters": f["parameters"],
        }
        for f in functions
    ]

def _tools_for(objective: str, allowed: list[dict] | None) -> list[dict]:
    if allowed is not None:
        return _trim(allowed)
    chosen = tool_index.select(objective, TOOLS_PER_STEP)
    if chosen is None:
        return _trim(manager.functions())
    return _trim(manager.functions_named(chosen))

async def run(
    objective: str,
    session_id: str,
    on_step: Callable[[str], None] | None = None,
    allowed: list[dict] | None = None,
) -> dict:
    """Let the model work one task to completion with whatever tools it wants."""
    tools = _tools_for(objective, allowed)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": objective},
    ]
    used: list[str] = []

    for _ in range(MAX_STEPS):
        message = await asyncio.wait_for(
            llm.complete_with_tools(messages, tools, model=llm.SYNTH_MODEL), _STEP_TIMEOUT
        )
        calls = message.get("tool_calls") or []
        if not calls:
            text = llm.strip_markdown(message.get("content") or "")
            return {"text": text, "tools": used}

        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": calls,
        })

        for call in calls[:3]:
            name = call["function"]["name"]
            used.append(name)
            if on_step:
                on_step(name)
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
                result = await asyncio.wait_for(
                    manager.call(name, args, session_id), _STEP_TIMEOUT
                )
            except Exception as exc:
                logger.info("day tool %r failed: %s", name, exc)
                result = json.dumps({"error": str(exc)[:200]})
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", name),
                "name": name,
                "content": (result or "")[:4000],
            })

    text = llm.strip_markdown(
        (await asyncio.wait_for(
            llm.complete_with_tools(messages, [], model=llm.SYNTH_MODEL), _STEP_TIMEOUT
        )).get("content") or ""
    )
    return {"text": text, "tools": used}
