import importlib
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.memory import create_connected_server_and_client_session

from app.host import elicitation

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "mcp_servers.json"
_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_INJECTED_PARAMS = ("session_id",)

# A server may expose this to hand the host a standing briefing about the user —
# what it already knows, before anyone asks. The model never sees it as a tool;
# the host folds what it returns into the prompt at connect time.
BRIEFING_TOOL = "briefing"

class ToolFailed(RuntimeError):
    """A server ran the tool and reported it as failed."""

def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            var = m.group(1)
            resolved = os.environ.get(var)
            if resolved is None:
                logger.warning("mcp_servers.json references unset env var %r", var)
                return ""
            return resolved
        return _VAR.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value

def _flatten_structured(structured: dict | None) -> str | None:
    if not structured:
        return None
    if structured.keys() == {"result"}:
        result = structured["result"]
        return result if isinstance(result, str) else json.dumps(result)
    return json.dumps(structured)

def _hide_injected(schema: dict) -> dict:
    props = schema.get("properties")
    if not props or not any(p in props for p in _INJECTED_PARAMS):
        return schema
    out = dict(schema)
    out["properties"] = {k: v for k, v in props.items() if k not in _INJECTED_PARAMS}
    if "required" in out:
        out["required"] = [r for r in out["required"] if r not in _INJECTED_PARAMS]
    return out

class MCPManager:
    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._servers: dict[str, dict] = {}
        self._route: dict[str, tuple[str, str]] = {}
        self._functions_cache: list[dict] | None = None
        self._functions_by_server_cache: dict[str, list[dict]] | None = None

    async def connect_all(self) -> None:
        config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
        for name, spec in config.get("mcpServers", {}).items():
            if spec.get("disabled"):
                continue
            try:
                await self._connect(name, spec)
            except Exception:
                logger.exception("skipping MCP server %r — failed to connect", name)
        self._build_routes()
        logger.info(
            "MCP host ready: %d server(s), %d tool(s)",
            len(self._servers), len(self._route),
        )

    def _register(self, name: str, session: ClientSession, tools: list, instructions: str) -> None:
        tools = list(tools)
        self._servers[name] = {
            "session": session,
            "tools": tools,
            "instructions": instructions,
            "by_name": {t.name: t for t in tools},
            # Voice sessions with a call currently running against this server,
            # so a question the server raises can be routed back to the right one.
            "callers": {},
        }

    def _elicitation_handler(self, name: str):
        """Route this server's questions to whoever it's currently working for."""
        async def handle(context, params) -> types.ElicitResult | types.ErrorData:
            if getattr(params, "mode", "form") != "form":
                return types.ErrorData(
                    code=types.INVALID_PARAMS,
                    message="Fraise only speaks form-mode elicitation.",
                )
            callers = (self._servers.get(name) or {}).get("callers") or {}
            if len(callers) != 1:
                # A question only means something if it's clear who is being
                # asked. With no call in flight, or several at once, there's no
                # honest answer to give, so the server is told so plainly.
                logger.warning(
                    "%r asked for input with %d call(s) in flight; declining", name, len(callers)
                )
                return types.ElicitResult(action="decline")

            session_id, tool = next(iter(callers.values()))
            action, content = await elicitation.request(
                session_id=session_id,
                server=name,
                tool=tool,
                message=params.message,
                schema=params.requestedSchema,
            )
            return types.ElicitResult(action=action, content=content)

        return handle

    async def _connect(self, name: str, spec: dict) -> None:
        spec = _expand_env(spec)
        kind = spec.get("type", "stdio")
        elicit = self._elicitation_handler(name)

        async with AsyncExitStack() as scoped:
            if kind == "builtin":
                # Builtin servers speak the protocol over an in-process transport
                # rather than being called directly, so that everything the spec
                # adds on top of a plain function call — elicitation included —
                # works the same whether a skill lives here or across a socket.
                module = importlib.import_module(spec["module"])
                server = getattr(module, spec.get("attr", "mcp"))
                session = await scoped.enter_async_context(
                    create_connected_server_and_client_session(server, elicitation_callback=elicit)
                )
            else:
                if kind == "http":
                    read, write, _ = await scoped.enter_async_context(
                        streamablehttp_client(spec["url"], headers=spec.get("headers"))
                    )
                else:
                    params = StdioServerParameters(
                        command=spec["command"],
                        args=spec.get("args", []),
                        env=spec.get("env"),
                        cwd=spec.get("cwd"),
                    )
                    read, write = await scoped.enter_async_context(stdio_client(params))

                session = await scoped.enter_async_context(
                    ClientSession(read, write, elicitation_callback=elicit)
                )

            # A server states its own conversational contract at initialize. For
            # third-party servers that can't know about Fraise, the config entry
            # supplies one instead — so a capability is still one config entry.
            handshake = await session.initialize()
            instructions = str(spec.get("instructions") or handshake.instructions or "").strip()

            tools = (await session.list_tools()).tools
            self._register(name, session, tools, instructions)
            self._stack.push_async_callback(scoped.pop_all().aclose)

        count = len(self._servers[name]["tools"])
        logger.info("connected %r (%s) — %d tool(s)", name, kind, count)

    def _build_routes(self) -> None:
        counts: dict[str, int] = {}
        for s in self._servers.values():
            for t in s["tools"]:
                counts[t.name] = counts.get(t.name, 0) + 1

        self._route.clear()
        for sname, s in self._servers.items():
            for t in s["tools"]:
                if t.name == BRIEFING_TOOL:
                    continue
                public = t.name if counts[t.name] == 1 else f"{sname}_{t.name}"
                public = _UNSAFE.sub("_", public)[:64]
                self._route[public] = (sname, t.name)

        self._functions_cache = None
        self._functions_by_server_cache = None

    def functions(self) -> list[dict]:
        if self._functions_cache is None:
            self._functions_cache = [
                {
                    "name": public,
                    "description": self._servers[sname]["by_name"][tname].description or "",
                    "parameters": _hide_injected(self._servers[sname]["by_name"][tname].inputSchema),
                }
                for public, (sname, tname) in self._route.items()
            ]
        return self._functions_cache

    def instructions_by_server(self) -> dict[str, str]:
        return {
            name: server["instructions"]
            for name, server in self._servers.items()
            if server["instructions"]
        }

    def functions_by_server(self) -> dict[str, list[dict]]:
        if self._functions_by_server_cache is None:
            out: dict[str, list[dict]] = {}
            for public, (sname, tname) in self._route.items():
                tool = self._servers[sname]["by_name"][tname]
                stripped = (tool.description or "").strip()
                description = stripped.splitlines()[0] if stripped else ""
                out.setdefault(sname, []).append({"name": public, "description": description})
            self._functions_by_server_cache = out
        return self._functions_by_server_cache

    async def call(
        self, public_name: str, arguments: dict[str, Any], session_id: str | None = None
    ) -> str:
        if public_name not in self._route:
            raise KeyError(f"unknown tool: {public_name!r}")

        sname, tname = self._route[public_name]
        server = self._servers[sname]

        tool = server["by_name"][tname]
        if "session_id" in (tool.inputSchema.get("properties") or {}):
            arguments = {**arguments, "session_id": session_id or ""}

        call_id = uuid4().hex
        server["callers"][call_id] = (session_id or "", public_name)
        try:
            result = await server["session"].call_tool(tname, arguments)
        finally:
            server["callers"].pop(call_id, None)

        text = "".join(getattr(b, "text", "") for b in (result.content or [])).strip()
        if result.isError:
            raise ToolFailed(text or "the tool failed without saying why")
        return _flatten_structured(getattr(result, "structuredContent", None)) or text

    async def briefings(self, session_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, server in self._servers.items():
            if BRIEFING_TOOL not in server["by_name"]:
                continue
            try:
                result = await server["session"].call_tool(
                    BRIEFING_TOOL, {"session_id": session_id}
                )
            except Exception:
                logger.warning("briefing from %r failed", name, exc_info=True)
                continue
            if result.isError:
                continue
            text = (
                _flatten_structured(getattr(result, "structuredContent", None))
                or "".join(getattr(b, "text", "") for b in (result.content or []))
            ).strip()
            if text:
                out[name] = text
        return out

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._servers.clear()
        self._route.clear()
        self._functions_cache = None
        self._functions_by_server_cache = None

manager = MCPManager()
