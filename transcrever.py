#!/usr/bin/env python3
"""Transcreve o microfone e o áudio do Discord e grava um log de texto."""

from __future__ import annotations

import argparse
import faulthandler
import os
import select
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

SR = 16000
BYTES_PER_SAMPLE = 2

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


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def cublas_directories() -> list[Path]:
    directories: list[Path] = []
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        directories.append(Path(conda_prefix) / "lib")
    home = Path.home()
    for base in (
        home / "anaconda3" / "envs",
        home / "miniconda3" / "envs",
        home / "mambaforge" / "envs",
        home / "miniforge3" / "envs",
    ):
        if base.is_dir():
            directories.extend(sorted(path for path in base.glob("*/lib") if path.is_dir()))
    directories.extend(
        [
            Path("/usr/local/cuda/lib64"),
            Path("/usr/local/lib/ollama/cuda_v12"),
        ]
    )
    return directories


def ensure_cuda_libraries() -> None:
    """CTranslate2 loads libcublas.so.12 at runtime. Re-exec once so the linker sees it."""
    chosen = next((path for path in cublas_directories() if (path / "libcublas.so.12").exists()), None)
    if chosen is None:
        return
    chosen_str = str(chosen)
    parts = [part for part in os.environ.get("LD_LIBRARY_PATH", "").split(":") if part]
    if chosen_str in parts:
        return
    os.environ["LD_LIBRARY_PATH"] = ":".join([chosen_str, *parts])
    os.execv(sys.executable, [sys.executable, *sys.argv])


def run_pactl(args: list[str]) -> str:
    return subprocess.check_output(["pactl", *args], text=True, stderr=subprocess.DEVNULL)


def pulse_blocks(kind: str) -> list[str]:
    text = run_pactl(["list", kind])
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("Sink Input #") or line.startswith("Source Output #"):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def block_index(block: str) -> str:
    return block.splitlines()[0].split("#", 1)[1].strip()


def block_field(block: str, prefix: str) -> str | None:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split(":", 1)[1].strip()
    return None


def is_discord_playback(block: str) -> bool:
    return "Discord" in block and "playStream" in block


def is_discord_mic(block: str) -> bool:
    return "Discord" in block and "recStream" in block


def source_name(index: str) -> str | None:
    for line in run_pactl(["list", "short", "sources"]).splitlines():
        parts = line.split("\t")
        if parts and parts[0] == index:
            return parts[1]
    return None


def find_discord_playback() -> str | None:
    for block in pulse_blocks("sink-inputs"):
        if is_discord_playback(block):
            return block_index(block)
    return None


def find_discord_mic() -> str:
    for block in pulse_blocks("source-outputs"):
        if is_discord_mic(block):
            source_index = block_field(block, "Source:")
            if source_index:
                name = source_name(source_index)
                if name:
                    return name
    return "@DEFAULT_SOURCE@"


def sink_input_alive(index: str) -> bool:
    try:
        text = run_pactl(["list", "short", "sink-inputs"])
    except subprocess.CalledProcessError:
        return False
    return any(line.split("\t", 1)[0] == index for line in text.splitlines() if line.strip())


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


@dataclass
class Channel:
    label: str
    autogain: bool
    parts: list[tuple[float, np.ndarray]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    proc: subprocess.Popen | None = None
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


class Log:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = directory / f"chamada-{stamp}.txt"
        self._fh = self.path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        current = directory / "atual.txt"
        if current.is_symlink() or current.exists():
            current.unlink()
        current.symlink_to(self.path.name)

    def write(self, when: datetime, label: str, text: str) -> None:
        line = f"[{when.strftime('%H:%M:%S')}] {label}: {text}"
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
        print(line, flush=True)

    def note(self, text: str) -> None:
        when = datetime.now()
        line = f"[{when.strftime('%H:%M:%S')}] -- {text}"
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
        print(line, flush=True)

    def header(self, lines: list[str]) -> None:
        with self._lock:
            for line in lines:
                self._fh.write(line + "\n")
            self._fh.write("\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        with self._lock:
            self._fh.close()


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


def transcribe_chunk(model, audio: np.ndarray, language: str, beam_size: int):
    segments, _info = model.transcribe(
        audio,
        language=None if language == "auto" else language,
        task="transcribe",
        beam_size=beam_size,
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


def parecord_command(extra: list[str]) -> list[str]:
    command = [
        "parecord",
        "--raw",
        "--rate=16000",
        "--format=s16le",
        "--channels=1",
        "--latency-msec=60",
        "--client-name=Transcricao da chamada",
        *extra,
    ]
    if Path("/usr/bin/stdbuf").exists():
        return ["stdbuf", "-o0", *command]
    return command


def capture_loop(channel: Channel, stop: threading.Event, mode: str) -> None:
    while not stop.is_set():
        try:
            if mode == "discord":
                stream_index = find_discord_playback()
                if not stream_index:
                    if channel.connected:
                        channel.connected = False
                    time.sleep(1.0)
                    continue
                command = parecord_command(
                    ["--stream-name=stt-discord", "--monitor-stream", stream_index]
                )
                watch_index = stream_index
            else:
                mic = find_discord_mic()
                command = parecord_command(["--stream-name=stt-mic", "--device", mic])
                watch_index = None
        except Exception as exc:
            print(f"[{channel.label}] erro ao achar o áudio: {exc}", flush=True)
            time.sleep(1.0)
            continue
        leftover = b""

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        channel.proc = proc
        channel.connected = True
        next_check = time.time() + 2.0
        try:
            assert proc.stdout is not None
            fd = proc.stdout.fileno()
            while not stop.is_set():
                ready, _, _ = select.select([fd], [], [], 1.0)
                if watch_index and time.time() >= next_check:
                    next_check = time.time() + 2.0
                    if proc.poll() is not None or not sink_input_alive(watch_index):
                        break
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                data = os.read(fd, SR * BYTES_PER_SAMPLE)
                if not data:
                    break
                raw = leftover + data
                if len(raw) % 2:
                    leftover = raw[-1:]
                    raw = raw[:-1]
                else:
                    leftover = b""
                if not raw:
                    continue
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                channel.append(samples, time.time())
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
            channel.proc = None
            channel.connected = False
        if not stop.is_set():
            time.sleep(0.4)


def emit_audio(
    model,
    channel: Channel,
    log: Log,
    language: str,
    window_s: float,
    overlap_s: float,
    max_s: float,
    flush: bool,
) -> None:
    min_samples = 1 if flush else int(window_s * SR)
    if flush:
        min_samples = int(0.4 * SR)
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
    beam = 1
    cutoff = audio.size / SR if flush else emit_until / SR
    try:
        segments = transcribe_chunk(model, prepared, language, beam)
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


def lines_before(segments, cutoff: float) -> tuple[list[tuple[float, str]], float]:
    """Linhas já fechadas e o ponto, em segundos, até onde o áudio pode ser descartado."""
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcreve a sua fala e o que você ouve no Discord, gravando um log de texto."
    )
    parser.add_argument("--model", default="small", help="tiny, base, small ou medium. Padrão: small")
    parser.add_argument("--lang", default="pt", help="Código do idioma, ou auto. Padrão: pt")
    parser.add_argument("--janela", type=float, default=6.0, help="Segundos acumulados antes de transcrever")
    parser.add_argument("--sobreposicao", type=float, default=1.5, help="Segundos repetidos entre blocos")
    parser.add_argument("--log-dir", default=str(script_dir() / "logs"))
    return parser.parse_args()


def write_pid() -> Path:
    path = script_dir() / "transcrever.pid"
    path.write_text(str(os.getpid()))
    return path


def remove_pid(path: Path) -> None:
    try:
        if path.is_file() and path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def main() -> int:
    faulthandler.enable(all_threads=True)
    ensure_cuda_libraries()
    pid_path = write_pid()
    args = parse_args()
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    log = Log(Path(args.log_dir))
    stop = threading.Event()

    def request_stop(_signum=None, _frame=None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    mic = Channel("eu", autogain=True)
    discord = Channel("discord", autogain=False)
    log.header(
        [
            "# Transcrição da chamada",
            f"# início: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "# eu = microfone usado pelo Discord",
            "# discord = áudio da chamada que você está ouvindo",
            "# as falas das outras pessoas ficam juntas na linha discord",
        ]
    )
    print(f"Log: {log.path}", flush=True)

    threads = [
        threading.Thread(target=capture_loop, args=(mic, stop, "mic"), name="mic", daemon=True),
        threading.Thread(target=capture_loop, args=(discord, stop, "discord"), name="discord", daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        model, model_desc = load_model(args.model)
    except Exception as exc:
        log.note(f"erro ao carregar o modelo: {exc}")
        stop.set()
        for channel in (mic, discord):
            if channel.proc is not None and channel.proc.poll() is None:
                channel.proc.terminate()
        log.close()
        remove_pid(pid_path)
        return 1
    log.note(f"modelo {model_desc}")

    discord_was_up = False
    mic_was_up = False
    warned_discord = False
    started = time.time()
    next_status = time.time() + 8
    try:
        while not stop.is_set():
            if discord.connected and not discord_was_up:
                log.note("áudio do Discord conectado")
                discord_was_up = True
            if not discord.connected and discord_was_up:
                log.note("áudio do Discord pausado, esperando a chamada")
                discord_was_up = False
            if not discord.connected and not warned_discord and time.time() - started > 6:
                log.note("ainda não achei o áudio do Discord. Entre numa chamada ou espere ela voltar.")
                warned_discord = True
            if mic.connected and not mic_was_up:
                log.note("microfone conectado")
                mic_was_up = True

            for channel in (mic, discord):
                if channel.pending_seconds() >= args.janela:
                    emit_audio(
                        model,
                        channel,
                        log,
                        args.lang,
                        args.janela,
                        args.sobreposicao,
                        max_s=20.0,
                        flush=False,
                    )
            if time.time() >= next_status:
                print(
                    f"ouvindo | eu {mic.pending_seconds():.1f}s rms={mic.last_rms:.3f} | "
                    f"discord {discord.pending_seconds():.1f}s rms={discord.last_rms:.3f}",
                    flush=True,
                )
                next_status = time.time() + 10
            time.sleep(0.15)
    finally:
        stop.set()
        for channel in (mic, discord):
            if channel.proc is not None and channel.proc.poll() is None:
                channel.proc.terminate()
        for channel in (mic, discord):
            while channel.pending_seconds() >= 0.4:
                before = channel.pending_seconds()
                emit_audio(
                    model,
                    channel,
                    log,
                    args.lang,
                    args.janela,
                    args.sobreposicao,
                    max_s=20.0,
                    flush=True,
                )
                if channel.pending_seconds() >= before - 0.05:
                    break
        log.note("transcrição encerrada")
        log.close()
        remove_pid(pid_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
