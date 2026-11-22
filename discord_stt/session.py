"""Estado da transcrição: parada, ouvindo ou pausada."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from discord_stt.audio import Channel, release_channel
from discord_stt.capture import capture_loop
from discord_stt.engine import emit_audio, flush_channel, load_model
from discord_stt.journal import CallLog

MODELOS = ("tiny", "base", "small", "medium")


class Sessao:
    def __init__(self, log_dir: Path, janela: float = 6.0, sobreposicao: float = 1.5) -> None:
        self.log_dir = log_dir
        self.janela = janela
        self.sobreposicao = sobreposicao
        self._lock = threading.RLock()
        self.estado = "parado"
        self.erro = ""
        self.modelo_nome = "small"
        self.idioma = "pt"
        self.modelo = None
        self.modelo_desc = ""
        self.log: CallLog | None = None
        self.channels: list[Channel] = []
        self.capture_stop = threading.Event()
        self._worker_stop = threading.Event()
        self._wake = threading.Event()
        self._busy = False
        self.linhas: list[dict[str, str]] = []
        self.discord_was_up = False
        self.mic_was_up = False
        self.warned_discord = False
        self.opened_at = 0.0
        self._worker = threading.Thread(target=self._loop, name="transcricao", daemon=True)
        self._worker.start()

    def iniciar(self, modelo: str, idioma: str) -> tuple[bool, str]:
        modelo = modelo.strip() or "small"
        idioma = idioma.strip() or "pt"
        if modelo not in MODELOS:
            return False, "Modelo desconhecido."
        if len(idioma) > 12:
            return False, "Idioma inválido."
        with self._lock:
            if self.estado != "parado":
                return False, "A transcrição já está aberta."
            self.estado = "iniciando"
            self.erro = ""
        try:
            if self.modelo is None or self.modelo_nome != modelo:
                model, desc = load_model(modelo)
                with self._lock:
                    self.modelo = model
                    self.modelo_desc = desc
                    self.modelo_nome = modelo
            self._abrir(idioma)
        except Exception as exc:
            with self._lock:
                self.estado = "parado"
                self.erro = str(exc)
            return False, str(exc)
        return True, "ouvindo"

    def pausar(self) -> tuple[bool, str]:
        with self._lock:
            if self.estado != "ouvindo":
                return False, "Só dá para pausar enquanto está ouvindo."
            channels = list(self.channels)
            log = self.log
            model = self.modelo
            idioma = self.idioma
            self.capture_stop.set()
            self.estado = "pausado"
        self._wait_idle()
        for channel in channels:
            release_channel(channel)
        if model is not None and log is not None:
            for channel in channels:
                flush_channel(model, channel, log, idioma, self.janela, self.sobreposicao)
            log.note("pausa")
        return True, "pausado"

    def continuar(self) -> tuple[bool, str]:
        with self._lock:
            if self.estado != "pausado":
                return False, "A transcrição não está pausada."
            log = self.log
            if log is None:
                self.estado = "parado"
                return False, "O log desta sessão já foi fechado."
            self.capture_stop = threading.Event()
            stop = self.capture_stop
            self.channels = [Channel("eu", autogain=True), Channel("discord", autogain=False)]
            channels = list(self.channels)
            self.discord_was_up = False
            self.mic_was_up = False
            self.warned_discord = False
            self.opened_at = time.time()
            self.estado = "ouvindo"
        self._start_capture(channels, stop)
        log.note("retomada")
        self._wake.set()
        return True, "ouvindo"

    def encerrar(self) -> tuple[bool, str]:
        with self._lock:
            if self.estado in {"parado", "iniciando"}:
                if self.estado == "iniciando":
                    return False, "A transcrição ainda está abrindo."
                return False, "Não havia transcrição aberta."
            channels = list(self.channels)
            log = self.log
            model = self.modelo
            idioma = self.idioma
            self.capture_stop.set()
            self.estado = "parado"
            self.channels = []
            self.log = None
        self._wait_idle()
        for channel in channels:
            release_channel(channel)
        if log is not None:
            if model is not None:
                for channel in channels:
                    flush_channel(model, channel, log, idioma, self.janela, self.sobreposicao)
            log.note("transcrição encerrada")
            with self._lock:
                self.linhas = log.snapshot()
            log.close()
        return True, "parado"

    def fechar(self) -> None:
        if self.estado != "parado":
            self.encerrar()
        self._worker_stop.set()
        self._wake.set()

    def _entries(self) -> list[dict[str, str]]:
        with self._lock:
            log = self.log
            saved = list(self.linhas)
        if log is not None:
            return log.snapshot()
        return saved

    def linhas_desde(self, desde: int) -> tuple[int, int, list[dict[str, str]]]:
        entries = self._entries()
        total = len(entries)
        inicio = max(0, min(desde, total))
        if desde == 0 and total > 800:
            inicio = total - 800
        return inicio, total, entries[inicio:]

    def foto(self, desde: int) -> dict:
        inicio, total, linhas = self.linhas_desde(desde)
        with self._lock:
            mic = next((channel for channel in self.channels if channel.label == "eu"), None)
            discord = next((channel for channel in self.channels if channel.label == "discord"), None)
            arquivo = str(self.log.path) if self.log is not None else ""
            return {
                "estado": self.estado,
                "erro": self.erro,
                "modelo": self.modelo_nome,
                "modelo_desc": self.modelo_desc,
                "idioma": self.idioma,
                "arquivo": arquivo,
                "desde": inicio,
                "total": total,
                "linhas": linhas,
                "rms_eu": mic.last_rms if mic is not None else 0.0,
                "rms_discord": discord.last_rms if discord is not None else 0.0,
                "mic_conectado": bool(mic and mic.connected),
                "discord_conectado": bool(discord and discord.connected),
            }

    def _abrir(self, idioma: str) -> None:
        log = CallLog(self.log_dir)
        stop = threading.Event()
        channels = [Channel("eu", autogain=True), Channel("discord", autogain=False)]
        with self._lock:
            self.log = log
            self.idioma = idioma
            self.linhas = []
            self.capture_stop = stop
            self.channels = channels
            self.discord_was_up = False
            self.mic_was_up = False
            self.warned_discord = False
            self.opened_at = time.time()
            self.estado = "ouvindo"
            desc = self.modelo_desc
        self._start_capture(channels, stop)
        log.note(f"modelo {desc}")
        self._wake.set()

    def _start_capture(self, channels: list[Channel], stop: threading.Event) -> None:
        for channel, mode in zip(channels, ("mic", "discord"), strict=True):
            threading.Thread(
                target=capture_loop,
                args=(channel, stop, mode),
                name=f"captura-{channel.label}",
                daemon=True,
            ).start()

    def _wait_idle(self) -> None:
        for _ in range(200):
            if not self._busy:
                return
            time.sleep(0.05)

    def _loop(self) -> None:
        next_status = 0.0
        while not self._worker_stop.is_set():
            self._wake.wait(0.15)
            self._wake.clear()
            with self._lock:
                if self.estado != "ouvindo" or self.modelo is None or self.log is None:
                    continue
                channels = list(self.channels)
                model = self.modelo
                log = self.log
                idioma = self.idioma
                self._busy = True
            try:
                self._observar(channels, log)
                for channel in channels:
                    if channel.pending_seconds() >= self.janela:
                        emit_audio(
                            model,
                            channel,
                            log,
                            idioma,
                            self.janela,
                            self.sobreposicao,
                            max_s=20.0,
                            flush=False,
                        )
                if time.time() >= next_status:
                    mic = next((channel for channel in channels if channel.label == "eu"), None)
                    discord = next((channel for channel in channels if channel.label == "discord"), None)
                    print(
                        "ouvindo | "
                        f"eu {mic.pending_seconds() if mic else 0:.1f}s rms={mic.last_rms if mic else 0:.3f} | "
                        f"discord {discord.pending_seconds() if discord else 0:.1f}s rms={discord.last_rms if discord else 0:.3f}",
                        flush=True,
                    )
                    next_status = time.time() + 10
            finally:
                self._busy = False

    def _observar(self, channels: list[Channel], log: CallLog) -> None:
        discord = next((channel for channel in channels if channel.label == "discord"), None)
        mic = next((channel for channel in channels if channel.label == "eu"), None)
        if discord is not None:
            if discord.connected and not self.discord_was_up:
                log.note("áudio do Discord conectado")
                self.discord_was_up = True
            if not discord.connected and self.discord_was_up:
                log.note("áudio do Discord pausado, esperando a chamada")
                self.discord_was_up = False
            if not discord.connected and not self.warned_discord and time.time() - self.opened_at > 6:
                log.note("ainda não achei o áudio do Discord. Entre numa chamada ou espere ela voltar.")
                self.warned_discord = True
        if mic is not None and mic.connected and not self.mic_was_up:
            log.note("microfone conectado")
            self.mic_was_up = True
