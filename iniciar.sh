#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
if [[ -f transcrever.pid ]] && kill -0 "$(cat transcrever.pid)" 2>/dev/null; then
  echo "Já existe um processo desta transcrição (pid $(cat transcrever.pid))."
  echo "A página fica em http://127.0.0.1:8765 quando este processo é a interface."
  echo "Para encerrar e subir de novo: $(pwd)/parar.sh && $(pwd)/iniciar.sh"
  exit 0
fi
# Solta do terminal: dois forks e uma sessão nova, para continuar depois que o shell fecha.
.venv/bin/python -c '
import os, sys
from pathlib import Path
root = Path.cwd()
args = sys.argv[1:]
if os.fork() > 0:
    os._exit(0)
os.setsid()
if os.fork() > 0:
    os._exit(0)
log_f = open(root / "logs" / "console.log", "a", buffering=1)
os.dup2(log_f.fileno(), 1)
os.dup2(log_f.fileno(), 2)
fd = os.open("/dev/null", os.O_RDONLY)
os.dup2(fd, 0)
python = sys.executable
os.execv(python, [python, "-u", "-m", "discord_stt", *args])
' "$@"
sleep 0.6
if [[ ! -f transcrever.pid ]] || ! kill -0 "$(cat transcrever.pid)" 2>/dev/null; then
  echo "Não consegui manter a transcrição rodando. Veja logs/console.log"
  exit 1
fi
echo "Interface ligada (pid $(cat transcrever.pid))."
echo "Abra: http://127.0.0.1:8765"
echo "Log: $(pwd)/logs/atual.txt"
echo "Parar: $(pwd)/parar.sh"
