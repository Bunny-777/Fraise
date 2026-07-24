import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("STT_MODEL", "base")
DEVICE = os.getenv("STT_DEVICE", "auto")
COMPUTE = os.getenv("STT_COMPUTE", "int8")
BEAM_SIZE = int(os.getenv("STT_BEAM_SIZE", "1"))
TEMPERATURE = float(os.getenv("STT_TEMPERATURE", "0.0"))
CPU_THREADS = int(os.getenv("STT_CPU_THREADS", str(max(1, (os.cpu_count() or 4) - 4))))

LIVE_MODEL_NAME = os.getenv("STT_LIVE_MODEL", "tiny")
LIVE_CPU_THREADS = int(os.getenv("STT_LIVE_CPU_THREADS", "2"))

SAMPLE_RATE = 16_000

_HALLUCINATIONS = {
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "thank you so much for watching",
    "please subscribe",
    "subtitles by the amara.org community",
    "you",
}

_model = None
_live_model = None
_load_lock = threading.Lock()
_live_load_lock = threading.Lock()
_transcribe_lock = threading.Lock()
_live_transcribe_lock = threading.Lock()


def is_loaded() -> bool:
    return _model is not None


def load():
    global _model
    with _load_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "loading STT model %s (device=%s compute=%s cpu_threads=%s)",
                MODEL_NAME, DEVICE, COMPUTE, CPU_THREADS,
            )
            _model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE, cpu_threads=CPU_THREADS)
    return _model


def load_live():
    global _live_model
    with _live_load_lock:
        if _live_model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "loading live caption model %s (device=%s compute=%s cpu_threads=%s)",
                LIVE_MODEL_NAME, DEVICE, COMPUTE, LIVE_CPU_THREADS,
            )
            _live_model = WhisperModel(
                LIVE_MODEL_NAME, device=DEVICE, compute_type=COMPUTE, cpu_threads=LIVE_CPU_THREADS
            )
    return _live_model


def transcribe_interim(audio: np.ndarray, language: str | None = None) -> str:
    model = load_live()
    with _live_transcribe_lock:
        segments, _info = model.transcribe(
            audio,
            language=language,
            beam_size=1,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        return " ".join(seg.text.strip() for seg in segments if seg.text.strip())


def transcribe(
    audio: np.ndarray,
    context: str = "",
    hints: list[str] | None = None,
    language: str | None = None,
) -> str:
    model = load()
    prompt_parts = []
    if hints:
        prompt_parts.append(", ".join(hints) + ".")
    if context:
        prompt_parts.append(context[-200:])
    prompt = " ".join(prompt_parts) or None

    with _transcribe_lock:
        segments, _info = model.transcribe(
            audio,
            language=language,
            beam_size=BEAM_SIZE,
            temperature=TEMPERATURE,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            no_speech_threshold=0.55,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        parts = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            if seg.no_speech_prob > 0.75 and seg.avg_logprob < -0.8:
                continue
            unsure = seg.no_speech_prob > 0.4 or len(audio) < 1.5 * SAMPLE_RATE
            if unsure and text.lower().strip(" .!?") in _HALLUCINATIONS:
                continue
            parts.append(text)
    return " ".join(parts).strip()
