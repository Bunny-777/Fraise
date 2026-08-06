"""MCP elicitation, held open so it can be spoken.

A server that's missing something pauses its own tool call and asks the host
for it. The host doesn't answer: it parks the suspended call here, hands the
question to the voice model to ask out loud in its own words, and resumes the
very same call once the user answers — by voice, or from the card in the app.

Nothing in here knows what any particular server is asking for. The question
text and the field types come from the server's schema; the host only renders
them and carries an answer back.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# A tool call suspended mid-elicitation holds a real coroutine open, so it can't
# wait forever — if the conversation moves on, the call is abandoned.
TTL_SECONDS = 240.0

_YES = {
    "yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "correct", "right", "do it", "go ahead", "send it", "please do", "affirmative", "true",
}
_NO = {
    "no", "n", "nope", "nah", "cancel", "stop", "don't", "dont", "do not",
    "never mind", "nevermind", "negative", "false", "wait",
}
_NUMBER = re.compile(r"-?\d+(?:[\d,]*\d)?(?:\.\d+)?")


@dataclass
class Pending:
    """One suspended tool call, waiting on an answer."""

    id: str
    session_id: str
    server: str
    tool: str
    message: str
    schema: dict
    future: asyncio.Future
    opened_at: float = field(default_factory=time.monotonic)
    # The tool call this elicitation came out of, attached by the host once it
    # decides to leave the call running. Awaited again after the answer lands.
    call: asyncio.Task | None = None
    # An exact answer submitted from the app's card, used verbatim in place of
    # whatever the model transcribes back.
    staged: tuple[str, dict | None] | None = None
    announced: bool = False


_pending: dict[str, Pending] = {}
_arrivals: dict[str, asyncio.Event] = {}


def _notify(session_id: str) -> None:
    event = _arrivals.get(session_id)
    if event is not None:
        event.set()


def _sweep() -> None:
    cutoff = time.monotonic() - TTL_SECONDS
    for p in [p for p in _pending.values() if p.opened_at < cutoff]:
        logger.info("elicitation %s (%s) expired unanswered", p.id, p.tool)
        discard(p, "expired")


# --- the server side: called from the MCP client's elicitation callback -------

async def request(
    session_id: str, server: str, tool: str, message: str, schema: dict
) -> tuple[str, dict | None]:
    """Park a server's question and wait for the answer it asked for.

    This runs on the MCP client's read loop, so the server that asked can't be
    reached again until it's answered. That's the reason the manager only routes
    a question when one call is in flight against a server: a second call to a
    server mid-question would sit behind this one.
    """
    _sweep()
    pending = Pending(
        id=uuid4().hex[:12],
        session_id=session_id,
        server=server,
        tool=tool,
        message=(message or "").strip(),
        schema=schema or {},
        future=asyncio.get_running_loop().create_future(),
    )
    _pending[pending.id] = pending
    logger.info("elicitation %s opened by %s.%s", pending.id, server, tool)
    _notify(session_id)
    try:
        return await pending.future
    finally:
        _pending.pop(pending.id, None)


# --- the host side ------------------------------------------------------------

async def next_for(session_id: str, tool: str) -> Pending:
    """Wait for a question raised by this session's in-flight call to `tool`.

    Matching on the tool keeps two calls running side by side from claiming each
    other's question; the manager only routes a question at all when a single
    call is in flight against that server, so the pair identifies it exactly.
    """
    while True:
        for pending in _pending.values():
            if pending.session_id == session_id and pending.tool == tool and not pending.announced:
                pending.announced = True
                return pending
        event = _arrivals.setdefault(session_id, asyncio.Event())
        event.clear()
        await event.wait()


def pending_for(session_id: str) -> Pending | None:
    """The oldest unanswered question in this session, if there is one."""
    _sweep()
    open_ones = sorted(
        (p for p in _pending.values() if p.session_id == session_id),
        key=lambda p: p.opened_at,
    )
    return open_ones[0] if open_ones else None


def by_id(elicitation_id: str) -> Pending | None:
    return _pending.get(elicitation_id)


def resolve(pending: Pending, action: str, content: dict | None) -> None:
    """Hand an answer back to the waiting server and let its tool run on."""
    if pending.future.done():
        return
    pending.future.set_result((action, content))
    logger.info(
        "elicitation %s %s after %.1fs — %s.%s resuming",
        pending.id, action, time.monotonic() - pending.opened_at, pending.server, pending.tool,
    )


def stage(pending: Pending, action: str, content: dict | None) -> None:
    """Hold an exact answer from the app's card until the model resumes the call."""
    pending.staged = (action, content)


def discard(pending: Pending, reason: str = "cancelled") -> None:
    """Give up on a question and cancel the tool call still hanging off it."""
    _pending.pop(pending.id, None)
    if not pending.future.done():
        pending.future.set_result(("cancel", None))
    if pending.call is not None and not pending.call.done():
        pending.call.cancel()
    logger.info("elicitation %s %s", pending.id, reason)


def close_session(session_id: str) -> None:
    """Drop everything still open for a session that's going away."""
    for pending in [p for p in _pending.values() if p.session_id == session_id]:
        discard(pending, "session closed")
    _arrivals.pop(session_id, None)


# --- rendering a server's schema -----------------------------------------------

def _flatten(spec: dict) -> dict:
    """Collapse the anyOf pydantic emits for optional fields down to one type."""
    if "anyOf" not in spec:
        return spec
    for option in spec["anyOf"]:
        if isinstance(option, dict) and option.get("type") != "null":
            return {**{k: v for k, v in spec.items() if k != "anyOf"}, **option}
    return spec


def describe(schema: dict) -> list[dict]:
    """Normalize a requested schema into flat, renderable field descriptors."""
    properties = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    fields = []
    for name, raw in properties.items():
        spec = _flatten(raw if isinstance(raw, dict) else {})
        choices = spec.get("enum")
        kind = spec.get("type") or ("string" if choices else "string")
        fields.append({
            "name": name,
            "type": "enum" if choices else kind,
            "label": spec.get("title") or name.replace("_", " "),
            "description": spec.get("description") or "",
            "choices": [str(c) for c in choices] if choices else None,
            "required": name in required,
            "default": spec.get("default"),
        })
    return fields


def summarize(fields: list[dict], content: dict | None) -> str:
    """A short, speakable rendering of what was answered."""
    if not content:
        return "nothing"
    labels = {f["name"]: f["label"] for f in fields}
    parts = []
    for name, value in content.items():
        if isinstance(value, bool):
            parts.append(f"{labels.get(name, name)}: {'yes' if value else 'no'}")
        else:
            parts.append(f"{labels.get(name, name)}: {value}")
    return ", ".join(parts)


# --- turning a spoken answer into the shape the server asked for ---------------

def _as_bool(text: str) -> bool | None:
    lowered = text.strip().lower().strip(".!,")
    if lowered in _YES or any(lowered.startswith(w + " ") for w in _YES):
        return True
    if lowered in _NO or any(lowered.startswith(w + " ") for w in _NO):
        return False
    return None


def _as_number(text: str, integer: bool) -> float | int | None:
    match = _NUMBER.search(text)
    if not match:
        return None
    value = float(match.group().replace(",", ""))
    return int(value) if integer else value


def _as_choice(text: str, choices: list[str]) -> str | None:
    lowered = text.strip().lower()
    for choice in choices:
        if lowered == choice.lower():
            return choice
    for choice in choices:
        if choice.lower() in lowered or lowered in choice.lower():
            return choice
    return None


def coerce(fields: list[dict], answer: str, values: dict | None) -> tuple[dict | None, str]:
    """Fit what the user said to the schema. Returns (content, problem)."""
    if values:
        content: dict[str, Any] = {}
        for f in fields:
            if f["name"] in values:
                one, problem = _coerce_one(f, str(values[f["name"]]))
                if problem:
                    return None, problem
                content[f["name"]] = one
        missing = [f["label"] for f in fields if f["required"] and f["name"] not in content]
        if missing:
            return None, f"Still missing: {', '.join(missing)}."
        return content, ""

    answer = (answer or "").strip()
    if not answer:
        return None, "No answer was given — ask the user again."

    wanted = [f for f in fields if f["required"]] or fields
    if len(wanted) > 1:
        labels = ", ".join(f["label"] for f in wanted)
        return None, (
            f"This question asks for several things at once ({labels}). Call this "
            "tool again with a values object holding one entry per field."
        )
    if not wanted:
        return {}, ""

    one, problem = _coerce_one(wanted[0], answer)
    if problem:
        return None, problem
    return {wanted[0]["name"]: one}, ""


def _coerce_one(f: dict, text: str) -> tuple[Any, str]:
    kind, label = f["type"], f["label"]
    if kind == "enum":
        choice = _as_choice(text, f["choices"] or [])
        if choice is None:
            return None, f"'{text}' isn't one of the choices for {label}: {', '.join(f['choices'] or [])}."
        return choice, ""
    if kind == "boolean":
        decided = _as_bool(text)
        if decided is None:
            return None, f"'{text}' isn't a clear yes or no for {label} — ask the user to confirm."
        return decided, ""
    if kind in ("number", "integer"):
        number = _as_number(text, kind == "integer")
        if number is None:
            return None, f"No number for {label} in '{text}' — ask the user again."
        return number, ""
    return text.strip(), ""
