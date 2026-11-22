"""Caminhos estáveis do projeto, independentes do diretório de trabalho."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LOGS = ROOT / "logs"
PID_FILE = ROOT / "transcrever.pid"
STOP_FILE = ROOT / "parar.pedido"
