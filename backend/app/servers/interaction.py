"""Shared elicitation sugar for Fraise's own MCP servers.

Any server can pause a call to ask for something it's missing, or to check
before it does something it can't take back. Both are plain MCP elicitation —
these are just the two shapes that come up over and over, so no server has to
write out a schema for "one string" or "yes or no" again.

Hosts that don't support elicitation get `None` (nothing asked) or `False`
(nothing confirmed), so a server built on these degrades quietly instead of
failing.
"""

from mcp.server.elicitation import AcceptedElicitation
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field, create_model


class _Confirmation(BaseModel):
    confirmed: bool = Field(title="Go ahead", description="Yes to go ahead, no to stop.")


async def ask(ctx: Context, question: str, *, about: str, hint: str = "") -> str | None:
    """Ask for one missing value. Returns None if the user won't say.

    `about` labels the field on screen — "Recipient", "City" — so the card the
    user sees names the thing being asked for, not just "answer".
    """
    schema = create_model(
        "Answer",
        answer=(str, Field(title=about, description=hint or question)),
    )
    result = await ctx.elicit(message=question, schema=schema)
    if isinstance(result, AcceptedElicitation):
        return (result.data.answer or "").strip() or None
    return None


async def confirm(ctx: Context, question: str) -> bool:
    """Check before doing something that can't be undone."""
    result = await ctx.elicit(message=question, schema=_Confirmation)
    return isinstance(result, AcceptedElicitation) and result.data.confirmed
