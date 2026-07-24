import json
import logging
import re

from app.servers.research import llm

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_SYSTEM = (
    "You clean up a raw voice dictation transcript. Fix capitalization, spacing, "
    "and punctuation, and break it into paragraphs where the speaker naturally "
    "paused between ideas. Preserve the speaker's own words and meaning exactly — "
    "never add, remove, or rephrase content, never invent facts.\n\n"
    "Also decide whether this was meant to become an email: the speaker addressed "
    "someone directly, asked to write or send an email, or dictated something in "
    "a clear letter format (greeting, message, sign-off).\n\n"
    'Return JSON exactly as: {"polished": "...", "is_email": true|false, '
    '"email": {"to": "..."|null, "subject": "...", "body": "..."} | null}\n\n'
    "For email.to: only fill it in if the speaker said a literal email address "
    "out loud — never invent or guess an address from a name alone; use null "
    "otherwise. The email body should be the polished message, formatted with a "
    "greeting and sign-off if the speaker implied one."
)


async def polish(raw_text: str) -> dict:
    fallback = {"polished": raw_text, "is_email": False, "email": None}
    if not raw_text.strip():
        return fallback

    try:
        raw = await llm.complete(_SYSTEM, raw_text, json_mode=True, model=llm.MODEL, max_tokens=2048)
        data = json.loads(raw)
    except Exception:
        logger.warning("dictation polish unavailable; falling back to raw text", exc_info=True)
        return fallback

    if not isinstance(data, dict):
        return fallback

    polished = str(data.get("polished") or "").strip() or raw_text
    is_email = bool(data.get("is_email"))
    email_raw = data.get("email")

    if is_email and isinstance(email_raw, dict):
        to = str(email_raw.get("to") or "").strip()
        email = {
            "to": to if _EMAIL_RE.match(to) else "",
            "subject": str(email_raw.get("subject") or "").strip()[:150],
            "body": str(email_raw.get("body") or polished).strip(),
        }
    else:
        is_email, email = False, None

    return {"polished": polished, "is_email": is_email, "email": email}
