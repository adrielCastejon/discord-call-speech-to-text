"""Carrega o modelo e transforma áudio em linhas de texto."""

from __future__ import annotations

import os
from datetime import datetime

import numpy as np

from discord_stt.audio import SR, Channel
from discord_stt.journal import CallLog

GARBAGE = (
    "amara.org",
    "legendas pela comunidade",
    "inscreva-se no canal",
    "obrigado por assistir",
    "thanks for watching",
    "thank you for watching",
    "sous-titres",
    "subtitles by",
    "subtítulos",
    "www.youtube",
    "youtube.com",
)


def usable_text(text: str) -> str | None:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) < 2:
        return None
    low = cleaned.lower().strip(" .!?…")
    if not low:
        return None
    if any(item in low for item in GARBAGE):
        return None
    words = low.split()
    if len(words) >= 4 and len(set(words)) == 1:
        return None
    return cleaned


def prepare_audio(audio: np.ndarray, autogain: bool) -> np.ndarray:
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if autogain and 0.008 < peak < 0.45:
        audio = audio * min(6.0, 0.5 / peak)
    return np.clip(audio, -1.0, 1.0)


def load_model(model_name: str):
    from faster_whisper import WhisperModel

    attempts = [
        (model_name, "cuda", "float16"),
        (model_name, "cuda", "int8_float16"),
        (model_name, "cpu", "int8"),
    ]
    last_error: Exception | None = None
    for name, device, compute in attempts:
        try:
            print(f"Carregando modelo {name} em {device} ({compute})...", flush=True)
            model = WhisperModel(name, device=device, compute_type=compute, cpu_threads=max(4, os.cpu_count() or 4))
            print(f"Modelo pronto: {name} / {device} / {compute}", flush=True)
            return model, f"{name} / {device} / {compute}"
        except Exception as exc:
            last_error = exc
            print(f"Não abriu em {device} ({compute}): {exc}", flush=True)
    raise RuntimeError(f"Não foi possível carregar o modelo: {last_error}")


def transcribe_chunk(model, audio: np.ndarray, language: str):
    segments, _info = model.transcribe(
        audio,
        language=None if language == "auto" else language,
        task="transcribe",
        beam_size=1,
        best_of=1,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 250},
        condition_on_previous_text=False,
        temperature=0.0,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        without_timestamps=False,
    )
    return list(segments)


def lines_before(segments, cutoff: float) -> tuple[list[tuple[float, str]], float]:
    selected: list[tuple[float, float, str]] = []
    hold_from: float | None = None

    def consider(word_start: float, word_end: float, text: str) -> None:
        nonlocal hold_from
        if word_end <= cutoff + 0.05:
            selected.append((word_start, word_end, text))
        elif hold_from is None or word_start < hold_from:
            hold_from = word_start

    for segment in segments:
        if segment.no_speech_prob is not None and segment.no_speech_prob > 0.6:
            continue
        if segment.avg_logprob is not None and segment.avg_logprob < -1.0:
            continue
        if segment.compression_ratio is not None and segment.compression_ratio > 2.4:
            continue
        if segment.words:
            for word in segment.words:
                if word.start is None:
                    continue
                consider(word.start, word.end or word.start, word.word)
        else:
            consider(segment.start, segment.end, segment.text)

    lines: list[tuple[float, str]] = []
    bucket: list[str] = []
    bucket_start: float | None = None
    last_end: float | None = None
    for word_start, word_end, word_text in selected:
        if last_end is not None and word_start - last_end > 1.0 and bucket:
            lines.append((bucket_start if bucket_start is not None else word_start, "".join(bucket)))
            bucket = []
            bucket_start = None
        if bucket_start is None:
            bucket_start = word_start
        bucket.append(word_text)
        last_end = word_end
    if bucket:
        lines.append((bucket_start if bucket_start is not None else 0.0, "".join(bucket)))

    if hold_from is None:
        commit_at = cutoff
    else:
        commit_at = min(cutoff, max(0.0, hold_from - 0.02))
    if commit_at < 0.8:
        commit_at = cutoff
    return lines, commit_at


def emit_audio(
    model,
    channel: Channel,
    log: CallLog,
    language: str,
    window_s: float,
    overlap_s: float,
    max_s: float,
    flush: bool,
) -> None:
    min_samples = int(0.4 * SR) if flush else int(window_s * SR)
    piece = channel.take(int(max_s * SR), min_samples)
    if piece is None:
        return
    start, audio = piece
    overlap = 0 if flush else int(overlap_s * SR)
    emit_until = audio.size if flush else max(1, audio.size - overlap)
    prepared = prepare_audio(audio, channel.autogain)
    rms = float(np.sqrt(np.mean(np.square(prepared)))) if prepared.size else 0.0
    if rms < 0.004:
        channel.commit(emit_until)
        return
    cutoff = audio.size / SR if flush else emit_until / SR
    try:
        segments = transcribe_chunk(model, prepared, language)
    except Exception as exc:
        print(f"[{channel.label}] falha na transcrição: {exc}", flush=True)
        channel.commit(emit_until)
        return
    lines, commit_at = lines_before(segments, cutoff)
    for line_start, text in lines:
        cleaned = usable_text(text)
        if not cleaned:
            continue
        log.write(datetime.fromtimestamp(start + line_start), channel.label, cleaned)
    commit_samples = audio.size if flush else max(1, int(commit_at * SR))
    channel.commit(min(commit_samples, audio.size))


def flush_channel(model, channel: Channel, log: CallLog, language: str, window_s: float, overlap_s: float) -> None:
    while channel.pending_seconds() >= 0.4:
        before = channel.pending_seconds()
        emit_audio(model, channel, log, language, window_s, overlap_s, max_s=20.0, flush=True)
        if channel.pending_seconds() >= before - 0.05:
            break
