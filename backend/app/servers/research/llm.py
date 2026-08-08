import asyncio
import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

MODEL = os.environ.get("RESEARCH_MODEL", "llama-3.1-8b-instant")
SYNTH_MODEL = os.environ.get("RESEARCH_SYNTH_MODEL", "llama-3.3-70b-versatile")

_TIMEOUT = httpx.Timeout(45.0, connect=10.0)
_RETRIES = 5

_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

def _clean(text: str) -> str:
    text = _THINK.sub("", text)
    if "<think>" in text.lower():
        return ""
    return text.strip()

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_BOLD = re.compile(r"(\*{1,3}|_{2,3})(.+?)\1", re.S)
_MD_CODE = re.compile(r"`+([^`]*)`+")
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET = re.compile(r"^[ \t]{0,3}[-*•][ \t]+", re.M)
_BARE_URL = re.compile(r"<?(?:https?://|www\.)[^\s<>]*[^\s<>.,;:!?)\]'\"]>?", re.I)
_WS = re.compile(r"[ \t]{2,}")

def strip_markdown(text: str) -> str:
    if not text:
        return ""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEAD.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_CODE.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\2", text)
    text = _BARE_URL.sub("", text)
    text = text.replace("*", "")
    text = _WS.sub(" ", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return text.strip(" -–—•*\t")

PLAIN_TEXT_RULE = (
    "Write PLAIN TEXT ONLY. No markdown of any kind: no asterisks, no bold, no "
    "square brackets, no links, no headings, no backticks. Never write a URL — the "
    "sources are listed separately, and a URL read aloud is unusable. Every line is "
    "an ordinary English sentence."
)

class LLMUnavailable(RuntimeError):
    pass

def friendly_error(exc: BaseException) -> str:
    """Provider errors are for logs. This is what a person is allowed to read."""
    text = str(exc)
    if "429" in text or "rate limit" in text.lower():
        return "The language model is rate limited right now — give it a minute and try again."
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in text.lower():
        return "That took too long to come back."
    if isinstance(exc, LLMUnavailable):
        return "The language model isn't reachable right now."
    return text[:160] or "Something went wrong there."

async def _message(payload: dict) -> dict:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMUnavailable("GROQ_API_KEY is not set")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(_RETRIES):
            r = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            if r.status_code == 429 and attempt < _RETRIES - 1:
                # The limit is per minute, so a few seconds is never long enough —
                # honour what the header asks for and wait it out.
                wait = float(r.headers.get("retry-after", 0)) or 2 ** (attempt + 2)
                logger.info("groq 429; retrying in %.1fs (attempt %d)", wait, attempt + 1)
                await asyncio.sleep(min(wait, 60))
                continue
            if r.status_code == 400 and "tool_use_failed" in r.text:
                return {"content": "", "tool_calls": []}
            if r.status_code >= 400:
                raise LLMUnavailable(f"Groq {r.status_code}: {r.text[:160]}")
            return r.json()["choices"][0]["message"]
    raise LLMUnavailable("Groq rate limit — out of retries")

async def complete(
    system: str, user: str, *, json_mode: bool = False, model: str = "",
    max_tokens: int | None = None,
) -> str:
    payload: dict = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    message = await _message(payload)
    return _clean(message.get("content") or "")

async def complete_with_tools(
    messages: list[dict], tools: list[dict], *, model: str = "", max_tokens: int | None = None,
) -> dict:
    payload: dict = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        payload["tools"] = [{"type": "function", "function": t} for t in tools]
        payload["tool_choice"] = "auto"
    if max_tokens:
        payload["max_tokens"] = max_tokens
    return await _message(payload)

async def complete_json(
    system: str, user: str, *, model: str = "", max_tokens: int | None = None
) -> dict:
    raw = await complete(system, user, json_mode=True, model=model, max_tokens=max_tokens)
    try:
        return json.loads(raw) if isinstance(json.loads(raw), dict) else {}
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(raw[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        logger.warning("synthesis returned non-JSON: %s", raw[:200])
        return {}
