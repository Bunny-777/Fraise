import re

_RULES = (
    (re.compile(r"\*\*|__"), "bold markers"),
    (re.compile(r"^\s{0,3}#{1,6}\s", re.M), "markdown heading"),
    (re.compile(r"^\s*[-*•]\s+", re.M), "bullet list"),
    (re.compile(r"\[[^\]]+\]\([^)]*\)"), "markdown link"),
    (re.compile(r"(?:https?://|www\.)\S+"), "spoken URL"),
    (re.compile(r"`"), "backtick"),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), "emoji"),
)

def problems(text: str) -> list[str]:
    return [label for pattern, label in _RULES if pattern.search(text or "")]

def is_speakable(text: str) -> bool:
    return not problems(text)
