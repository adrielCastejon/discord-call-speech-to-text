#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f transcrever.pid ]]; then
  echo "Não havia transcrição rodando."
  exit 0
fi
pid="$(cat transcrever.pid)"
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.4
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  echo "Transcrição parada."
else
  echo "O processo já tinha terminado."
fi
rm -f transcrever.pid
