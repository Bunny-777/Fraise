import os
import threading

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

SAMPLE_RATE = 16_000
SILENCE_S = float(os.getenv("STT_SILENCE_S", "0.45"))
MAX_UTTERANCE_S = 28.0
EVAL_EVERY_S = float(os.getenv("STT_EVAL_EVERY_S", "0.2"))
MIN_BUFFER_S = float(os.getenv("STT_MIN_BUFFER_S", "0.6"))
PAD_S = 0.15
IDLE_TRIM_S = 8.0

_VAD = VadOptions(
    min_silence_duration_ms=300,
    min_speech_duration_ms=200,
    speech_pad_ms=80,
)

_PAD = int(PAD_S * SAMPLE_RATE)


class Segmenter:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self._since_eval = 0
        self._lock = threading.Lock()

    def feed(self, pcm16: bytes) -> list[np.ndarray]:
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            self._chunks.append(audio)
            self._samples += len(audio)
            self._since_eval += len(audio)
            if self._since_eval < EVAL_EVERY_S * SAMPLE_RATE or self._samples < MIN_BUFFER_S * SAMPLE_RATE:
                return []
            self._since_eval = 0
            return self._evaluate(force=False)

    def flush(self) -> list[np.ndarray]:
        with self._lock:
            return self._evaluate(force=True)

    def peek_tail(self, max_seconds: float) -> np.ndarray | None:
        with self._lock:
            if not self._samples:
                return None
            if len(self._chunks) > 1:
                self._chunks = [np.concatenate(self._chunks)]
            tail = self._chunks[0]
            max_samples = int(max_seconds * SAMPLE_RATE)
            return tail[-max_samples:] if len(tail) > max_samples else tail

    def _evaluate(self, force: bool) -> list[np.ndarray]:
        if not self._samples:
            return []
        buffer = np.concatenate(self._chunks)
        self._chunks = [buffer]
        speech = get_speech_timestamps(buffer, _VAD)

        if not speech:
            if force or self._samples > IDLE_TRIM_S * SAMPLE_RATE:
                self._reset(buffer[-SAMPLE_RATE:] if not force else None)
            return []

        last_end = speech[-1]["end"]
        tail_gap = len(buffer) - last_end

        if force or tail_gap >= SILENCE_S * SAMPLE_RATE:
            cut = min(len(buffer), last_end + _PAD)
            segment = buffer[max(0, speech[0]["start"] - _PAD) : cut]
            self._reset(None if force else buffer[cut:])
            return [segment]

        if len(buffer) >= MAX_UTTERANCE_S * SAMPLE_RATE:
            if len(speech) >= 2:
                cut = min(len(buffer), speech[-2]["end"] + _PAD)
                segment = buffer[max(0, speech[0]["start"] - _PAD) : cut]
                self._reset(buffer[max(cut, speech[-1]["start"] - _PAD) :])
            else:
                segment = buffer[max(0, speech[0]["start"] - _PAD) :]
                self._reset(None)
            return [segment]

        return []

    def _reset(self, keep: np.ndarray | None) -> None:
        self._chunks = [keep] if keep is not None and len(keep) else []
        self._samples = len(keep) if keep is not None else 0
        self._since_eval = 0
