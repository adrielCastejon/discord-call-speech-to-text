"""Canal de áudio em memória, compartilhado pela captura e pela transcrição."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

SR = 16000


@dataclass
class Channel:
    label: str
    autogain: bool
    parts: list[tuple[float, np.ndarray]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    proc: object | None = None
    closer: Callable[[], None] | None = None
    connected: bool = False
    last_rms: float = 0.0
    samples_pending: int = 0

    def append(self, samples: np.ndarray, ended_at: float) -> None:
        if samples.size == 0:
            return
        started_at = ended_at - (samples.size / SR)
        with self.lock:
            if self.parts:
                prev_start, prev = self.parts[-1]
                prev_end = prev_start + (prev.size / SR)
                if abs(started_at - prev_end) < 0.35:
                    self.parts[-1] = (prev_start, np.concatenate([prev, samples]))
                else:
                    self.parts.append((started_at, samples))
            else:
                self.parts.append((started_at, samples))
            self.samples_pending = sum(part.size for _, part in self.parts)
            self.last_rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0

    def pending_seconds(self) -> float:
        with self.lock:
            return self.samples_pending / SR

    def take(self, max_samples: int, min_samples: int) -> tuple[float, np.ndarray] | None:
        with self.lock:
            if not self.parts:
                return None
            start, audio = self.parts[0]
            if audio.size < min_samples and len(self.parts) == 1:
                return None
            count = min(audio.size, max_samples)
            return start, audio[:count].copy()

    def commit(self, count: int) -> None:
        with self.lock:
            if not self.parts or count <= 0:
                return
            start, audio = self.parts[0]
            if count >= audio.size:
                self.parts.pop(0)
            else:
                self.parts[0] = (start + count / SR, audio[count:])
            self.samples_pending = sum(part.size for _, part in self.parts)


def release_channel(channel: Channel) -> None:
    proc = channel.proc
    if proc is not None and getattr(proc, "poll", lambda: 0)() is None:
        terminate = getattr(proc, "terminate", None)
        if terminate is not None:
            try:
                terminate()
            except Exception:
                pass
    closer = channel.closer
    channel.closer = None
    if closer is not None:
        try:
            closer()
        except Exception:
            pass
