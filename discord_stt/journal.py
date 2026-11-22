"""Arquivo de log da chamada e a cópia usada pela interface."""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path


class CallLog:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = directory / f"chamada-{stamp}.txt"
        self._fh = self.path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self._mirror = None
        self.entries: list[dict[str, str]] = []
        current = directory / "atual.txt"
        try:
            if current.is_symlink() or current.exists():
                current.unlink()
            current.symlink_to(self.path.name)
        except OSError:
            self._mirror = current.open("a", encoding="utf-8", buffering=1)
        if sys.platform == "win32":
            origem_mic = "# eu = microfone padrão do Windows"
            origem_discord = "# discord = áudio do processo Discord; se o isolamento falhar, o áudio dos alto-falantes"
        else:
            origem_mic = "# eu = microfone usado pelo Discord"
            origem_discord = "# discord = áudio da chamada que você está ouvindo"
        self.header(
            [
                "# Transcrição da chamada",
                f"# início: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                origem_mic,
                origem_discord,
                "# as falas das outras pessoas ficam juntas na linha discord",
            ]
        )

    def _record(self, text: str) -> None:
        self._fh.write(text)
        self._fh.flush()
        os.fsync(self._fh.fileno())
        if self._mirror is not None:
            self._mirror.write(text)
            self._mirror.flush()
            os.fsync(self._mirror.fileno())

    def _add(self, entry: dict[str, str], line: str) -> None:
        with self._lock:
            self.entries.append(entry)
            self._record(line + "\n")
        print(line, flush=True)

    def write(self, when: datetime, label: str, text: str) -> None:
        hora = when.strftime("%H:%M:%S")
        self._add(
            {"tipo": "fala", "hora": hora, "rotulo": label, "texto": text},
            f"[{hora}] {label}: {text}",
        )

    def note(self, text: str) -> None:
        hora = datetime.now().strftime("%H:%M:%S")
        self._add(
            {"tipo": "nota", "hora": hora, "rotulo": "", "texto": text},
            f"[{hora}] -- {text}",
        )

    def header(self, lines: list[str]) -> None:
        with self._lock:
            self._record("".join(line + "\n" for line in lines) + "\n")

    def snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(item) for item in self.entries]

    def close(self) -> None:
        with self._lock:
            if self._fh.closed:
                return
            self._fh.close()
            if self._mirror is not None and not self._mirror.closed:
                self._mirror.close()
