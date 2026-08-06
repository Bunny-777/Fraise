import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_URL = os.environ.get("DEEPGRAM_PRERECORDED_URL", "https://api.deepgram.com/v1/listen")
MAX_BYTES = 100 * 1024 * 1024

_TIMEOUT = httpx.Timeout(600.0, connect=10.0)

class TranscriptionFailed(RuntimeError):
    pass

def _params() -> dict:
    return {
        "model": os.environ.get("DEEPGRAM_TRANSCRIBE_MODEL", "nova-3"),
        "language": os.environ.get("DEEPGRAM_TRANSCRIBE_LANGUAGE", "multi"),
        "smart_format": "true",
        "punctuate": "true",
        "paragraphs": "true",
        "diarize": "true",
        "utterances": "true",
    }

def _plain(payload: dict) -> str:
    channels = payload.get("results", {}).get("channels") or []
    alternatives = (channels[0] if channels else {}).get("alternatives") or []
    return ((alternatives[0] if alternatives else {}).get("transcript") or "").strip()

def _dialogue(payload: dict) -> str:
    lines: list[str] = []
    speaker: int | None = None
    said: list[str] = []

    for utterance in payload.get("results", {}).get("utterances") or []:
        text = (utterance.get("transcript") or "").strip()
        if not text:
            continue
        who = int(utterance.get("speaker") or 0)
        if said and who != speaker:
            lines.append(f"Speaker {speaker + 1}: {' '.join(said)}")
            said = []
        speaker = who
        said.append(text)

    if said and speaker is not None:
        lines.append(f"Speaker {speaker + 1}: {' '.join(said)}")
    return "\n".join(lines) or _plain(payload)

async def transcribe(raw: bytes, content_type: str = "") -> str:
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        raise TranscriptionFailed("DEEPGRAM_API_KEY isn't set, so I can't transcribe audio")
    if not raw:
        raise TranscriptionFailed("that recording is empty")
    if len(raw) > MAX_BYTES:
        raise TranscriptionFailed("that recording is too large — keep it under 100 MB")

    headers = {
        "Authorization": f"Token {key}",
        "Content-Type": content_type or "application/octet-stream",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.post(API_URL, params=_params(), headers=headers, content=raw)
        except httpx.HTTPError as exc:
            logger.warning("deepgram pre-recorded request failed: %s", exc)
            raise TranscriptionFailed("I couldn't reach the transcription service") from exc

    if response.status_code >= 400:
        logger.warning("deepgram %s: %s", response.status_code, response.text[:200])
        raise TranscriptionFailed(f"transcription failed ({response.status_code})")

    try:
        payload = response.json()
    except ValueError as exc:
        raise TranscriptionFailed("the transcription service returned something unreadable") from exc

    return _dialogue(payload)
