import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import anyio
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.host import elicitation
from app.host.mcp_manager import manager
from app.servers.memory import consolidate, store as memory_store

_ACTION_TIMEOUT = 90

_TOOL_TIMEOUT = 15

# The host's own tool. Servers never see it: it exists so the model has a way to
# hand back an answer to a question a server stopped mid-call to ask.
ANSWER_TOOL = "provide_requested_input"
ANSWER_FUNCTION = {
    "name": ANSWER_TOOL,
    "description": (
        "Answer the question a tool stopped to ask, and let that tool finish. Call "
        "this only after a tool has come back asking for input and you've asked the "
        "user out loud and heard their reply. Pass exactly what they said as answer. "
        "If the tool asked for several things at once, pass values instead: a JSON "
        "object with one entry per requested field. Set declined to true if the user "
        "refuses or wants to drop it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "What the user said, in their own words.",
            },
            "values": {
                "type": "string",
                "description": "JSON object of field name to value, when several were asked for.",
            },
            "declined": {
                "type": "boolean",
                "description": "True if the user declined to answer or called it off.",
            },
        },
        "required": [],
    },
}

logger = logging.getLogger(__name__)

DG_URL = os.environ.get("DEEPGRAM_AGENT_URL", "wss://agent.deepgram.com/v1/agent/converse")
MAX_TOOL_STEPS = 10

INPUT_RATE = 16_000
OUTPUT_RATE = 24_000

PROMPT = (
    "You are Fraise, an intelligent voice assistant. You can chat naturally and do "
    "math. Chain as many tool calls as needed to fully complete a task before you "
    "speak. Never stop mid-chain to ask if you should continue; just finish the job.\n\n"
    "If someone asks for something you have no tool for, say plainly that it isn't "
    "connected here and offer to help another way. Never invent data for a "
    "capability you don't have, and never claim you did something you didn't.\n\n"
    "When a tool asks you something: a tool can come back needing something it "
    "doesn't have instead of a result — you'll get a needs_input reply with a "
    "question in it. That tool is paused mid-job, waiting. Ask the user that "
    "question out loud, in your own words, in one short natural sentence — never "
    "read the raw question or mention tools, fields, or forms. When they answer, "
    "call provide_requested_input with exactly what they said, and the paused tool "
    "picks up where it left off. It may ask again; that's fine, ask again too. If "
    "they say no or change their mind, call it with declined set to true. Never "
    "guess an answer on their behalf, and never re-call the original tool to "
    "supply it — provide_requested_input is the only way back into a paused call.\n\n"
    "How you think: be decisive. Work out what the person actually wants and go do "
    "it — don't ask permission for something they've already asked for, and don't "
    "offer to do a thing instead of doing it. If a request is ambiguous in a way "
    "that changes the answer, ask one sharp question; otherwise pick the sensible "
    "reading and act. Reach for your tools early and often: it is always better to "
    "look something up than to half-remember it. Chain as many calls as the job "
    "needs before you speak.\n\n"
    "Anticipate. If someone asks about internships, they want to know where and how "
    "to apply. If they ask about a product, they want the price and whether it's any "
    "good. Answer the question behind the question, and volunteer the one detail they "
    "would obviously ask for next — but only one, and only if it earns its place.\n\n"
    "Sound great out loud. Keep replies short — usually one or two sentences — warm, "
    "clear, and confident. Say the single most useful thing and stop; don't pad, don't "
    "recap, don't narrate what you're about to do, and don't tack on a follow-up "
    "question unless you truly need one to continue. Never open with filler like "
    "'Sure!', 'Great question', or 'Let me see' — lead with the answer. Speak like a "
    "sharp friend who knows the subject, not a call centre.\n\n"
    "If the user starts talking while you're speaking, they're interrupting on "
    "purpose — stop immediately and listen. No lists, no markdown, no emoji, no "
    "asterisks for emphasis — never write ** around a word; say numbers, dates, and "
    "symbols the way a person would speak them. Never invent results, and never "
    "claim you did something you didn't."
)

def _clean_name(raw: str) -> str:
    name = "".join(ch for ch in raw if ch.isprintable() and ch not in "{}").strip()
    return name[:40]

def _clean_instructions(raw: str) -> str:
    text = "".join(ch for ch in raw if ch.isprintable() or ch == "\n").strip()
    return text[:1500]

def _describe_capabilities() -> str:
    lines = []
    for sname, tools in manager.functions_by_server().items():
        bullets = "; ".join(t["description"] for t in tools if t["description"])
        if bullets:
            lines.append(f"- {sname}: {bullets}")
    return "\n".join(lines)

def _skill_contracts() -> str:
    """Every connected server's own rules, in its own words.

    The host never states what a skill does or when to reach for it — the skill
    says so itself at connect time, so adding one needs no change here.
    """
    blocks = [
        f"{sname}:\n{text}"
        for sname, text in manager.instructions_by_server().items()
    ]
    if not blocks:
        return ""
    return (
        "\n\nYour skills, each with its own rules — follow them exactly when that "
        "skill is in play:\n\n" + "\n\n".join(blocks)
    )

async def _recent_context(session_id: str) -> str:
    if not session_id:
        return ""
    turns = await anyio.to_thread.run_sync(memory_store.recent_turns, session_id)
    if not turns:
        return ""
    return "\n".join(f"{'User' if role == 'user' else 'Fraise'}: {content}" for role, content in turns)

async def _build_settings(
    session_id: str = "",
    user_name: str = "",
    greet: bool = True,
    assistant_name: str = "",
    instructions: str = "",
    personas: str = "",
    voice: str = "",
) -> dict:
    name = _clean_name(user_name)
    persona = _clean_name(assistant_name)
    speaker = persona if persona and persona.lower() != "fraise" else "Fraise"

    prompt = PROMPT
    if name:
        prompt = (
            f"The user's name is {name}. Address them by their first name naturally "
            "and warmly — greet them by name and use it occasionally, but don't "
            "overuse it or start every sentence with it.\n\n"
        ) + prompt

    custom = _clean_instructions(instructions)
    if custom:
        prompt = (
            "Custom instructions for this assistant — follow them throughout the "
            f"conversation:\n{custom}\n\n"
        ) + prompt
    if speaker != "Fraise":
        prompt = (
            f"Your name is {speaker}. Introduce yourself and refer to yourself as "
            f"{speaker}, not Fraise. You still run on Fraise, but to this user you "
            f"are {speaker}.\n\n"
        ) + prompt

    greeting = (
        f"Hey{f' {name}' if name else ''}, you made it — I'm {speaker}. It's "
        "really good to hear you. What can I do for you today?"
    )

    prompt = prompt + _skill_contracts()

    # Context arrives before the first word, rather than waiting for the model to
    # think of asking for it.
    briefings = await manager.briefings(session_id) if session_id else {}
    if briefings:
        prompt = prompt + (
            "\n\nWhat you already know, before they say anything:\n\n"
            + "\n\n".join(briefings.values())
        )

    now = datetime.now(timezone.utc)
    prompt = prompt + (
        f"\n\nToday's date is {now:%A, %B %-d, %Y}, current time {now:%H:%M} UTC. "
        "Use this for anything involving dates, deadlines, or 'today'/'tomorrow' — "
        "never rely on your training data for the current date. Convert to the "
        "user's local time only if you know their timezone; otherwise say UTC."
    )

    capabilities = _describe_capabilities()
    if capabilities:
        prompt = prompt + (
            "\n\nWhat you can do: if asked what you can do, your skills, or your "
            "features, describe these warmly in your own words as natural sentences "
            "grouped by theme — never recite raw tool names or read this list "
            "verbatim.\n" + capabilities
        )

    other = [
        p for p in (_clean_name(x) for x in personas.split(",") if x.strip())
        if p and p.lower() != speaker.lower()
    ]
    if other:
        prompt = prompt + (
            "\n\nThis user's other assistants, for switching: " + ", ".join(other) + "."
        )

    context = await _recent_context(session_id)
    if context:
        prompt = prompt + (
            "\n\nRecent conversation history with this user, carried over from "
            "before this connection — use it for continuity (don't ask something "
            "they already told you), but only bring it up out loud if it's "
            "relevant or they ask what you talked about:\n" + context
        )

    listen_model = os.environ.get("DEEPGRAM_LISTEN_MODEL", "nova-3")
    listen_provider = {"type": "deepgram", "model": listen_model}
    if listen_model.startswith("flux"):
        listen_provider["version"] = "v2"
        listen_provider["language_hints"] = [
            h.strip() for h in os.environ.get("DEEPGRAM_LANGUAGE_HINTS", "en,hi").split(",") if h.strip()
        ]
        listen_provider["eot_threshold"] = float(os.environ.get("DEEPGRAM_EOT_THRESHOLD", "0.7"))
        listen_provider["eot_timeout_ms"] = int(os.environ.get("DEEPGRAM_EOT_TIMEOUT_MS", "15000"))
    else:
        listen_provider["language"] = os.environ.get("DEEPGRAM_LISTEN_LANGUAGE", "en-IN")

    agent: dict = {
        "listen": {"provider": listen_provider},
        "think": {
            "provider": {
                "type": os.environ.get("DEEPGRAM_THINK_TYPE", "open_ai"),
                "model": os.environ.get("DEEPGRAM_THINK_MODEL", "gpt-4o-mini"),
            },
            "prompt": prompt,
            "functions": [*manager.functions(), ANSWER_FUNCTION],
        },
        "speak": {"provider": {"type": "deepgram", "model": voice or os.environ.get("DEEPGRAM_VOICE", "aura-2-thalia-en")}},
    }
    if greet:
        agent["greeting"] = greeting

    return {
        "type": "Settings",
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": INPUT_RATE},
            "output": {"encoding": "linear16", "sample_rate": OUTPUT_RATE, "container": "none"},
        },
        "agent": agent,
    }

def _translate(text: str) -> str | None:
    """Turn a message from the browser into something Deepgram understands."""
    try:
        event = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text

    kind = event.get("type")
    if kind == "document_uploaded":
        raw_filename = event.get("filename") or ""
        recording = event.get("kind") == "recording"
        name = Path(raw_filename).stem if raw_filename else (
            "the recording" if recording else "the document"
        )
        what = "a call recording" if recording else "a document"
        read = "go through it" if recording else "read it"
        return json.dumps({
            "type": "InjectUserMessage",
            "content": f'I just uploaded {what} called "{name}". Ask me whether I want '
                       f"you to {read} or research what it's about, then wait for my "
                       "answer before doing either.",
        })

    if kind == "elicitation_response":
        return _stage_elicitation(event)

    return text

def _stage_elicitation(event: dict) -> str | None:
    """Take an answer typed into the card and say it on the user's behalf.

    The values are held exactly as submitted so speech can't garble an address or
    a choice, but the model still drives the conversation: it hears that the
    question was answered and calls back in to resume the paused tool.
    """
    pending = elicitation.by_id(str(event.get("id") or ""))
    if pending is None:
        return None

    fields = elicitation.describe(pending.schema)
    raw = event.get("content")
    submitted = raw if isinstance(raw, dict) else None
    content, problem = elicitation.coerce(fields, "", submitted) if submitted else (None, "")
    declined = event.get("action") == "decline" or content is None

    if problem:
        logger.warning("card answer for %s didn't fit the schema: %s", pending.id, problem)

    elicitation.stage(pending, "decline" if declined else "accept", content)
    if declined:
        spoken = "I've cancelled that — never mind."
    else:
        spoken = f"I filled that in on screen — {elicitation.summarize(fields, content)}."
    return json.dumps({
        "type": "InjectUserMessage",
        "content": f"{spoken} Use it to finish what you were doing.",
    })

async def _invoke(fn: dict, session_id: str) -> str:
    """Run one tool to completion, turning any failure into speakable text."""
    try:
        args = json.loads(fn.get("arguments") or "{}")
        return await manager.call(fn["name"], args, session_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("tool %r failed", fn.get("name"))
        return json.dumps({"error": str(exc)})

async def _run_tool(fn: dict, session_id: str) -> str:
    try:
        return await asyncio.wait_for(_invoke(fn, session_id), _TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("tool %r timed out after %ds", fn.get("name"), _TOOL_TIMEOUT)
        return json.dumps({"error": "That took too long, so I stopped. Please try again."})

async def _settle(
    call: asyncio.Task, session_id: str, tool: str
) -> tuple[str | None, elicitation.Pending | None]:
    """Wait on a tool call, but hand back early if it stops to ask something.

    A call that pauses on an elicitation keeps running — the coroutine is sitting
    inside the server, holding the work it has already done — so the timeout only
    applies while the tool is working, never while a person is being asked.
    """
    asking = asyncio.create_task(elicitation.next_for(session_id, tool))
    try:
        done, _ = await asyncio.wait(
            {call, asking}, timeout=_TOOL_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        if not asking.done():
            asking.cancel()

    if call in done:
        if call.cancelled():
            return json.dumps({"error": "That was dropped before it finished."}), None
        return call.result(), None
    if asking in done:
        pending = asking.result()
        pending.call = call
        return None, pending

    call.cancel()
    logger.warning("tool %r timed out after %ds", tool, _TOOL_TIMEOUT)
    return json.dumps({"error": "That took too long, so I stopped. Please try again."}), None

def _extract_action(content: str) -> dict | None:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return data.get("_action") if isinstance(data, dict) else None

def _strip_action(content: str) -> str:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content
    if isinstance(data, dict):
        data.pop("_action", None)
    return json.dumps(data)

async def _resolve_action(fn: dict, session_id: str, browser: WebSocket, action: dict) -> str:
    await browser.send_text(json.dumps(action))
    logger.info("tool %r requested action %r; waiting up to %ds",
                fn.get("name"), action.get("type"), _ACTION_TIMEOUT)
    deadline = time.monotonic() + _ACTION_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(1.5)
        content = await _run_tool(fn, session_id)
        if _extract_action(content) is None:
            return content
    return json.dumps({"error": "The action timed out. Please try again."})

async def _ask_out_loud(browser: WebSocket, pending: elicitation.Pending) -> str:
    """Put the question on screen, and give the model its half of the job."""
    fields = elicitation.describe(pending.schema)
    await browser.send_text(json.dumps({
        "type": "elicitation",
        "id": pending.id,
        "server": pending.server,
        "tool": pending.tool,
        "message": pending.message,
        "fields": fields,
    }))
    logger.info("%s is asking: %s", pending.tool, pending.message)
    return json.dumps({
        "status": "needs_input",
        "question": pending.message,
        "asks_for": fields,
        "next_step": (
            "Ask the user this out loud in your own words, then call "
            f"{ANSWER_TOOL} with their reply. Do not call {pending.tool} again."
        ),
    })

async def _answer(browser: WebSocket, fn: dict, session_id: str) -> tuple[str, elicitation.Pending | None]:
    """Feed the model's answer back into the paused call and let it finish."""
    pending = elicitation.pending_for(session_id)
    if pending is None or pending.call is None:
        logger.info("model answered a question that isn't open: %s", fn.get("arguments"))
        return json.dumps({"error": "Nothing is waiting on an answer right now."}), None

    args = json.loads(fn.get("arguments") or "{}")
    fields = elicitation.describe(pending.schema)

    if pending.staged is not None:
        # The user filled the card in the app, so use those values exactly rather
        # than whatever came back through speech.
        action, content = pending.staged
    elif args.get("declined"):
        action, content = "decline", None
    else:
        raw_values = args.get("values")
        try:
            values = json.loads(raw_values) if isinstance(raw_values, str) and raw_values.strip() else raw_values
        except json.JSONDecodeError:
            values = None
        content, problem = elicitation.coerce(
            fields, str(args.get("answer") or ""), values if isinstance(values, dict) else None
        )
        if problem:
            # The question stays open; the model gets told what's still missing.
            return json.dumps({"status": "needs_input", "question": pending.message, "problem": problem}), None
        action = "accept"

    elicitation.resolve(pending, action, content)
    result, next_pending = await _settle(pending.call, session_id, pending.tool)
    if next_pending is not None:
        return await _ask_out_loud(browser, next_pending), next_pending

    await browser.send_text(json.dumps({
        "type": "elicitation_resolved",
        "id": pending.id,
        "outcome": action,
        "answer": elicitation.summarize(fields, content) if action == "accept" else "",
    }))
    return result or "", None

async def _run_one_function_call(dg, browser: WebSocket, fn: dict, session_id: str) -> None:
    fn_id = fn.get("id")
    fn_name = fn.get("name", "")
    try:
        if fn_name == ANSWER_TOOL:
            content, _ = await _answer(browser, fn, session_id)
        else:
            call = asyncio.create_task(_invoke(fn, session_id))
            content, pending = await _settle(call, session_id, fn_name)
            if pending is not None:
                content = await _ask_out_loud(browser, pending)

        action = _extract_action(content)
        if action:
            if action.get("once"):
                await browser.send_text(json.dumps(action))
                content = _strip_action(content)
            else:
                content = await _resolve_action(fn, session_id, browser, action)
    except Exception as exc:
        logger.exception("function call %r crashed outside the tool's own error handling", fn_name)
        content = json.dumps({"error": str(exc)})

    if fn_id is None:
        logger.warning("function call %r had no id; can't send a response", fn_name)
        return
    await dg.send(json.dumps({
        "type": "FunctionCallResponse",
        "id": fn_id,
        "name": fn_name,
        "content": content,
    }))

async def _handle_function_call(dg, browser: WebSocket, message: dict, session_id: str) -> None:
    functions = message.get("functions", [])
    await asyncio.gather(*(_run_one_function_call(dg, browser, fn, session_id) for fn in functions))

async def bridge(
    browser: WebSocket,
    session_id: str = "",
    user_name: str = "",
    greet: bool = True,
    assistant_name: str = "",
    instructions: str = "",
    personas: str = "",
    voice: str = "",
) -> None:
    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        await browser.send_json({"type": "error", "message": "DEEPGRAM_API_KEY is not set"})
        return

    async with websockets.connect(DG_URL, additional_headers={"Authorization": f"Token {api_key}"}) as dg:
        await dg.send(json.dumps(await _build_settings(
            session_id, user_name, greet, assistant_name, instructions, personas, voice
        )))

        async def browser_to_dg() -> None:
            while True:
                msg = await browser.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                if (data := msg.get("bytes")) is not None:
                    await dg.send(data)
                elif (text := msg.get("text")) is not None:
                    if (translated := _translate(text)) is not None:
                        await dg.send(translated)

        async def dg_to_browser() -> None:
            step_count = 0
            turn_count = 0
            async for message in dg:
                if isinstance(message, (bytes, bytearray)):
                    await browser.send_bytes(message)
                    continue
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")

                if event_type in ("UserStartedSpeaking", "ConversationText"):
                    step_count = 0

                if event_type == "ConversationText":
                    role, content = event.get("role"), event.get("content")
                    if role in ("user", "assistant") and content:
                        await anyio.to_thread.run_sync(memory_store.log_turn, session_id, role, content)
                        turn_count += 1
                        consolidate.maybe_consolidate(session_id, turn_count)

                if event_type == "FunctionCallRequest":
                    if step_count >= MAX_TOOL_STEPS:
                        logger.warning("tool step cap (%d) reached; aborting chain", MAX_TOOL_STEPS)
                        for fn in event.get("functions", []):
                            await dg.send(json.dumps({
                                "type": "FunctionCallResponse",
                                "id": fn["id"],
                                "name": fn["name"],
                                "content": json.dumps({"error": "tool step limit reached"}),
                            }))
                    else:
                        step_count += 1
                        await _handle_function_call(dg, browser, event, session_id)

                await browser.send_text(message)

        b2d = asyncio.create_task(browser_to_dg())
        d2b = asyncio.create_task(dg_to_browser())
        try:
            done, pending = await asyncio.wait({b2d, d2b}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        except WebSocketDisconnect:
            b2d.cancel()
            d2b.cancel()
        finally:
            # Nobody is left to answer, so let any half-finished tool call go.
            elicitation.close_session(session_id)
