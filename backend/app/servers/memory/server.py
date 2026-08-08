import anyio
from mcp.server.fastmcp import FastMCP

from . import store

mcp = FastMCP("memory", streamable_http_path="/", instructions=(
    "When the user tells you to remember, note, or keep track of something, call the "
    "remember tool. When a question might depend on something they told you before — "
    "a preference, a name, a detail — call recall first, then answer. Use forget when "
    "they ask you to drop something."
))

@mcp.tool(description=(
    "Save something the user wants remembered for later.\n\n"
    "content: the fact or preference to store, in plain language. Call this whenever "
    "the user says to remember, note, or keep track of something."
))
async def remember(content: str, session_id: str = "") -> str:
    if not session_id:
        return "I couldn't tell which session this is, so I can't save that right now."
    await anyio.to_thread.run_sync(store.remember, session_id, content)
    return "Got it — I'll remember that."

@mcp.tool(description=(
    "Look up what the user has asked Fraise to remember.\n\n"
    "query: words to search for. Leave blank to get their most recent memories. Call "
    "this before answering anything that might depend on what they've told you."
))
async def recall(query: str = "", session_id: str = "") -> str:
    if not session_id:
        return ""
    memories = await anyio.to_thread.run_sync(store.recall, session_id, query)
    if not memories:
        return "I don't have anything saved about that yet."
    return "Here's what I remember: " + "; ".join(memories) + "."

@mcp.tool(description="Delete saved memories that match the given words.\n\nquery: words identifying what to forget.")
async def forget(query: str, session_id: str = "") -> str:
    if not session_id:
        return "I couldn't tell which session this is, so I can't change anything."
    count = await anyio.to_thread.run_sync(store.forget, session_id, query)
    if not count:
        return "I didn't find anything matching that to forget."
    return f"Done — I forgot {count} thing{'s' if count != 1 else ''}."
