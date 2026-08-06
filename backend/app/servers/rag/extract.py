import io
from pathlib import Path

from pypdf import PdfReader

SUPPORTED = {".txt", ".md", ".pdf"}
AUDIO = {".mp3", ".m4a", ".mp4", ".wav", ".aac", ".flac", ".ogg", ".opus", ".webm", ".mov", ".amr"}

def is_audio(filename: str) -> bool:
    return Path(filename).suffix.lower() in AUDIO

def extract_text(filename: str, raw: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(
            f"unsupported file type {ext!r} — use {', '.join(sorted(SUPPORTED | AUDIO))}"
        )
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(raw))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.strip()
