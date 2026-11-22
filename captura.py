"""Captura o microfone e o áudio do Discord no Linux e no Windows."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

SR = 16000

DISCORD_EXECUTABLES = {
    "discord",
    "discord.exe",
    "discordptb",
    "discordptb.exe",
    "discordcanary",
    "discordcanary.exe",
    "discorddevelopment",
    "discorddevelopment.exe",
}


def close_once(action) -> Callable[[], None]:
    done = False
    lock = threading.Lock()

    def close() -> None:
        nonlocal done
        with lock:
            if done:
                return
            done = True
        action()

    return close


def capture_loop(channel: Any, stop, mode: str) -> None:
    if sys.platform == "win32":
        capture_loop_windows(channel, stop, mode)
    else:
        capture_loop_linux(channel, stop, mode)


def to_mono_16k(data: bytes, rate: int, channels: int, sample_format: str, pending: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if not data:
        return np.zeros(0, np.float32), pending
    width = 2 if sample_format == "int16" else 4
    data = data[: len(data) - (len(data) % width)]
    if not data:
        return np.zeros(0, np.float32), pending
    if sample_format == "int16":
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    else:
        samples = np.frombuffer(data, dtype="<f4").astype(np.float32, copy=True)
    channels = max(1, channels)
    usable = samples.size - (samples.size % channels)
    if usable <= 0:
        return np.zeros(0, np.float32), pending
    mono = samples[:usable].reshape(-1, channels).mean(axis=1)
    mono = np.concatenate([pending, mono]) if pending.size else mono
    if rate == SR:
        return mono.astype(np.float32, copy=False), np.zeros(0, np.float32)
    if rate <= 0:
        return np.zeros(0, np.float32), mono
    factor = rate / SR
    out_count = int(mono.size / factor)
    if out_count <= 0:
        return np.zeros(0, np.float32), mono
    positions = (np.arange(out_count) + 0.5) * factor
    consumed = min(mono.size, int(positions[-1]) + 1)
    converted = np.interp(positions, np.arange(mono.size), mono).astype(np.float32)
    return converted, mono[consumed:]


def choose_discord_pid(processes: list[tuple[int, int, str]]) -> int | None:
    discord = {
        pid: ppid
        for pid, ppid, name in processes
        if name.lower() in DISCORD_EXECUTABLES
    }
    if not discord:
        return None
    children: dict[int, list[int]] = {pid: [] for pid in discord}
    for pid, ppid in discord.items():
        if ppid in children and ppid != pid:
            children[ppid].append(pid)

    def tree_size(pid: int, seen: set[int] | None = None) -> int:
        seen = set() if seen is None else seen
        if pid in seen:
            return 0
        seen.add(pid)
        return 1 + sum(tree_size(child, seen) for child in children.get(pid, []))

    roots = [pid for pid, ppid in discord.items() if ppid not in discord]
    if not roots:
        roots = list(discord)
    return max(roots, key=tree_size)


def _windows_processes() -> list[tuple[int, int, str]]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value or snapshot == -1:
        raise OSError("Não consegui listar os processos do Windows")
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    found: list[tuple[int, int, str]] = []
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            found.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def find_discord_pid() -> int | None:
    return choose_discord_pid(_windows_processes())


def process_alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            import os

            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


class WasapiInput:
    def __init__(self, loopback: bool) -> None:
        import pyaudiowpatch as pyaudio

        self.pyaudio = pyaudio
        self.pa = pyaudio.PyAudio()
        device = self._device(loopback)
        self.rate = int(device["defaultSampleRate"])
        self.channels = max(1, int(device["maxInputChannels"] or 1))
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self.stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            frames_per_buffer=max(256, self.rate // 20),
            input=True,
            input_device_index=int(device["index"]),
            stream_callback=self._callback,
        )
        self.stream.start_stream()

    def _device(self, loopback: bool) -> dict:
        if loopback:
            getter = getattr(self.pa, "get_default_wasapi_loopback", None)
            if getter is not None:
                try:
                    return getter()
                except Exception:
                    pass
            wasapi = self.pa.get_host_api_info_by_type(self.pyaudio.paWASAPI)
            speakers = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            for candidate in self.pa.get_loopback_device_info_generator():
                if speakers["name"] in candidate["name"]:
                    return candidate
            raise RuntimeError("Não achei o loopback dos alto-falantes")
        try:
            wasapi = self.pa.get_host_api_info_by_type(self.pyaudio.paWASAPI)
            return self.pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        except Exception:
            return self.pa.get_default_input_device_info()

    def _callback(self, in_data, _frame_count, _time_info, _status):
        try:
            self.queue.put_nowait(bytes(in_data))
        except queue.Full:
            pass
        return (None, self.pyaudio.paContinue)

    def read(self, timeout: float) -> bytes | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        try:
            if self.stream.is_active():
                self.stream.stop_stream()
            self.stream.close()
        finally:
            self.pa.terminate()


def open_process_tap(pid: int, on_data):
    import proctap

    capture_cls = getattr(proctap, "ProcessAudioCapture", None)
    if capture_cls is not None:
        tap = capture_cls(pid, on_data=on_data)
        tap.start()
        return tap
    proc_tap = getattr(proctap, "ProcTap", None)
    if proc_tap is None:
        raise ImportError("proc-tap não expõe ProcessAudioCapture nem ProcTap")
    config = proctap.StreamConfig(sample_rate=48000, channels=2)
    tap = proc_tap(pid, config, on_data=on_data)
    tap.start()
    return tap


def tap_format(tap) -> tuple[int, int, str]:
    getter = getattr(tap, "get_format", None) or getattr(tap, "format", None)
    info = getter() if callable(getter) else getter if isinstance(getter, dict) else {}
    if not isinstance(info, dict):
        info = {}
    rate = int(info.get("sample_rate", 48000))
    channels = int(info.get("channels", 2))
    kind = str(info.get("sample_format", "float32")).lower()
    if "int" in kind or int(info.get("bits_per_sample", 32)) == 16:
        return rate, channels, "int16"
    return rate, channels, "float32"


def capture_loop_windows(channel: Any, stop, mode: str) -> None:
    use_loopback = False
    announced_wait = False
    failures = 0
    last_error = ""
    while not stop.is_set():
        try:
            if mode == "mic":
                _capture_mic_windows(channel, stop)
                continue
            if not use_loopback:
                pid = find_discord_pid()
                if pid is None:
                    channel.connected = False
                    if not announced_wait:
                        print("[discord] esperando o Discord abrir", flush=True)
                        announced_wait = True
                    time.sleep(1.0)
                    continue
                try:
                    _capture_discord_process(channel, stop, pid)
                    failures = 0
                    continue
                except Exception as exc:
                    failures += 1
                    print(f"[discord] não isolei o processo ({exc})", flush=True)
                    if failures < 3:
                        time.sleep(1.0)
                        continue
                    use_loopback = True
            _capture_loopback_windows(channel, stop)
        except ImportError as exc:
            print(f"[{channel.label}] dependência ausente: {exc}", flush=True)
            print("No Windows, instale com: pip install -r requirements.txt", flush=True)
            return
        except Exception as exc:
            message = str(exc)
            if message != last_error:
                print(f"[{channel.label}] erro ao capturar áudio: {message}", flush=True)
                last_error = message
            channel.connected = False
            if not stop.is_set():
                time.sleep(2.0)


def _capture_mic_windows(channel: Any, stop) -> None:
    source = WasapiInput(loopback=False)
    pending = np.zeros(0, np.float32)
    close = close_once(source.close)
    channel.closer = close
    channel.connected = True
    try:
        while not stop.is_set():
            data = source.read(0.5)
            if not data:
                continue
            samples, pending = to_mono_16k(data, source.rate, source.channels, "int16", pending)
            channel.append(samples, time.time())
    finally:
        close()
        channel.closer = None
        channel.connected = False


def _capture_discord_process(channel: Any, stop, pid: int) -> None:
    audio: queue.Queue[bytes] = queue.Queue(maxsize=50)

    def on_data(pcm: bytes, _frames: int) -> None:
        if pcm:
            try:
                audio.put_nowait(bytes(pcm))
            except queue.Full:
                pass

    tap = open_process_tap(pid, on_data)
    rate, channels, sample_format = tap_format(tap)
    pending = np.zeros(0, np.float32)
    close = close_once(tap.close)
    channel.closer = close
    channel.connected = True
    try:
        while not stop.is_set() and process_alive(pid):
            try:
                data = audio.get(timeout=0.5)
            except queue.Empty:
                continue
            samples, pending = to_mono_16k(data, rate, channels, sample_format, pending)
            channel.append(samples, time.time())
    finally:
        close()
        channel.closer = None
        channel.connected = False


def _capture_loopback_windows(channel: Any, stop) -> None:
    source = WasapiInput(loopback=True)
    pending = np.zeros(0, np.float32)
    close = close_once(source.close)
    channel.closer = close
    channel.connected = True
    print("[discord] gravando o áudio dos alto-falantes padrão", flush=True)
    try:
        while not stop.is_set():
            data = source.read(0.5)
            if not data:
                continue
            samples, pending = to_mono_16k(data, source.rate, source.channels, "int16", pending)
            channel.append(samples, time.time())
    finally:
        close()
        channel.closer = None
        channel.connected = False


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
        elif current:
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


def find_discord_playback() -> str | None:
    for block in pulse_blocks("sink-inputs"):
        if "Discord" in block and "playStream" in block:
            return block_index(block)
    return None


def find_discord_mic() -> str:
    for block in pulse_blocks("source-outputs"):
        if "Discord" in block and "recStream" in block:
            source_index = block_field(block, "Source:")
            if source_index:
                name = source_name(source_index)
                if name:
                    return name
    return "@DEFAULT_SOURCE@"


def source_name(index: str) -> str | None:
    for line in run_pactl(["list", "short", "sources"]).splitlines():
        parts = line.split("\t")
        if parts and parts[0] == index:
            return parts[1]
    return None


def sink_input_alive(index: str) -> bool:
    try:
        text = run_pactl(["list", "short", "sink-inputs"])
    except subprocess.CalledProcessError:
        return False
    return any(line.split("\t", 1)[0] == index for line in text.splitlines() if line.strip())


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


def capture_loop_linux(channel: Any, stop, mode: str) -> None:
    import os
    import select

    while not stop.is_set():
        try:
            if mode == "discord":
                stream_index = find_discord_playback()
                if not stream_index:
                    if channel.connected:
                        channel.connected = False
                    time.sleep(1.0)
                    continue
                command = parecord_command(["--stream-name=stt-discord", "--monitor-stream", stream_index])
                watch_index = stream_index
            else:
                command = parecord_command(["--stream-name=stt-mic", "--device", find_discord_mic()])
                watch_index = None
        except Exception as exc:
            print(f"[{channel.label}] erro ao achar o áudio: {exc}", flush=True)
            time.sleep(1.0)
            continue
        leftover = b""
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
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
                data = os.read(fd, SR * 2)
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
