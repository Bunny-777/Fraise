import logging
import os

from app.host.mcp_manager import manager

logger = logging.getLogger(__name__)

# Below this, every tool is declared directly and nothing is collapsed — the
# flat list is still the sharpest option at small scale, and this keeps the
# voice path byte-for-byte what it was.
FLAT_LIMIT = int(os.environ.get("VOICE_TOOL_LIMIT", "24"))

PREFIX = "use_"

def _delegate_name(server: str) -> str:
    return PREFIX + server.replace("-", "_")

def server_for(delegate: str) -> str:
    if not delegate.startswith(PREFIX):
        return ""
    wanted = delegate[len(PREFIX):]
    for server in manager.functions_by_server():
        if server.replace("-", "_") == wanted:
            return server
    return ""

def _delegate(server: str, tools: list[dict], contract: str) -> dict:
    names = ", ".join(t["name"].replace("_", " ") for t in tools)
    return {
        "name": _delegate_name(server),
        "description": (
            f"Handle anything to do with {server}. It can: {names}. "
            f"{contract[:400]} "
            "Pass the user's request in your own words and it will be carried out; "
            "you get back what happened."
        )[:1024],
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "What the user wants, in a full sentence, with every detail they gave.",
                }
            },
            "required": ["request"],
        },
    }

def voice_functions() -> list[dict]:
    """The tool list the voice model sees.

    Deepgram fixes `think.functions` when the session opens and gives no way to
    swap them per utterance, so per-turn retrieval is impossible here. What is
    possible is hierarchy: keep the small skills declared directly and collapse
    the big ones behind one delegate each, then resolve the real tool in a second
    hop where the full schemas are back in play.
    """
    functions = manager.functions()
    if len(functions) <= FLAT_LIMIT:
        return functions

    by_server = manager.functions_by_server()
    contracts = manager.instructions_by_server()
    ranked = sorted(by_server.items(), key=lambda kv: len(kv[1]))

    out: list[dict] = []
    budget = FLAT_LIMIT
    for server, tools in ranked:
        if len(tools) <= budget - (len(ranked) - len(out)):
            out.extend(manager.functions_named([t["name"] for t in tools]))
            budget -= len(tools)
        else:
            out.append(_delegate(server, tools, contracts.get(server, "")))
            budget -= 1
    return out

async def run(delegate: str, request: str, session_id: str) -> str:
    from app.host import agent_loop

    server = server_for(delegate)
    if not server:
        return "That skill isn't connected."
    tools = manager.functions_named(
        [t["name"] for t in manager.functions_by_server().get(server, [])]
    )
    if not tools:
        return "That skill has nothing to run."
    outcome = await agent_loop.run(request, session_id, allowed=tools)
    return outcome["text"] or "Done."
