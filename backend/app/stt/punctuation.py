import re

_COMMANDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bnew paragraph\b", re.I), "\n\n"),
    (re.compile(r"\bnew line\b", re.I), "\n"),
    (re.compile(r"\b(full stop|period)\b", re.I), "."),
    (re.compile(r"\bcomma\b", re.I), ","),
    (re.compile(r"\bquestion mark\b", re.I), "?"),
    (re.compile(r"\b(exclamation point|exclamation mark)\b", re.I), "!"),
    (re.compile(r"\bcolon\b", re.I), ":"),
    (re.compile(r"\bsemicolon\b", re.I), ";"),
    (re.compile(r"\bopen quote\b", re.I), " “"),
    (re.compile(r"\bclose quote\b", re.I), "”"),
    (re.compile(r"\bopen parenthesis\b", re.I), " ("),
    (re.compile(r"\bclose parenthesis\b", re.I), ")"),
    (re.compile(r"\b(dash|hyphen)\b", re.I), "—"),
    (re.compile(r"\bellipsis\b", re.I), "…"),
    (re.compile(r"\bat sign\b", re.I), "@"),
]

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:”])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_BLANK_LINE = re.compile(r"\n{3,}")
_LEADING_LINE_SPACE = re.compile(r"\n[ \t]+")
_TRAILING_LINE_SPACE = re.compile(r"[ \t]+\n")
_AFTER_NEWLINE = re.compile(r"(\n+)([a-z])")
_TERMINAL = (".", "!", "?", "”", "…", ":")


def apply_voice_commands(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _COMMANDS:
        text = pattern.sub(repl, text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _TRAILING_LINE_SPACE.sub("\n", text)
    text = _LEADING_LINE_SPACE.sub("\n", text)
    text = _MULTI_BLANK_LINE.sub("\n\n", text)
    return text.strip()


def finalize_segment(text: str) -> str:
    text = apply_voice_commands(text)
    if not text:
        return text
    text = text[0].upper() + text[1:]
    text = _AFTER_NEWLINE.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    if text[-1] not in _TERMINAL:
        text += "."
    return text


def join_segments(segments: list[str]) -> str:
    return apply_voice_commands(" ".join(s for s in segments if s))
