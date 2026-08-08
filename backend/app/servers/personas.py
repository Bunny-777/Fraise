import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("personas", streamable_http_path="/", instructions=(
    "This user may keep several assistants, each with its own separate memory and "
    "documents. If they clearly ask to switch to one — 'switch to my work "
    "assistant', 'go to Personal' — call switch_assistant with that assistant's "
    "name. Only switch when they ask; don't offer unprompted."
))

@mcp.tool(description=(
    "Switch the active voice assistant to the one the user names.\n\n"
    "name: the assistant to switch to, e.g. \"Work\" or \"Personal\". Call this only "
    "when the user clearly asks to switch assistants or personas. Each assistant has "
    "its own separate memory and documents."
))
def switch_assistant(name: str) -> str:
    return json.dumps({
        "_action": {"type": "switch_assistant", "name": name, "once": True},
        "spoken": f"Okay, switching to {name}.",
    })
