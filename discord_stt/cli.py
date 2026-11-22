"""Linha de comando: sobe a interface ou transcreve direto no terminal."""

from __future__ import annotations

import argparse
import faulthandler
import os
import signal
import sys
import time

from discord_stt.cuda import ensure_cuda_libraries
from discord_stt.paths import LOGS, PID_FILE, STOP_FILE
from discord_stt.server import serve
from discord_stt.session import Sessao


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcreve a sua fala e o que você ouve no Discord, gravando um log de texto."
    )
    parser.add_argument("--model", default="small", help="tiny, base, small ou medium. Padrão: small")
    parser.add_argument("--lang", default="pt", help="Código do idioma, ou auto. Padrão: pt")
    parser.add_argument("--janela", type=float, default=6.0, help="Segundos acumulados antes de transcrever")
    parser.add_argument("--sobreposicao", type=float, default=1.5, help="Segundos repetidos entre blocos")
    parser.add_argument("--log-dir", default=str(LOGS))
    parser.add_argument("--host", default="127.0.0.1", help="Endereço da interface. Padrão: 127.0.0.1")
    parser.add_argument("--porta", type=int, default=8765, help="Porta da interface. Padrão: 8765")
    parser.add_argument("--sem-interface", action="store_true", help="Transcreve no terminal, sem a página")
    parser.add_argument("--sem-pid", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()))


def remove_pid() -> None:
    try:
        if PID_FILE.is_file() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def run_headless(args: argparse.Namespace) -> int:
    sessao = Sessao(args.log_dir_path, janela=args.janela, sobreposicao=args.sobreposicao)
    stop = False

    def request_stop(_signum=None, _frame=None) -> None:
        nonlocal stop
        stop = True

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, request_stop)
        except (ValueError, OSError):
            pass
    ok, message = sessao.iniciar(args.model, args.lang)
    if not ok:
        print(message, flush=True)
        return 1
    try:
        while not stop:
            if STOP_FILE.exists():
                try:
                    STOP_FILE.unlink()
                except OSError:
                    pass
                break
            time.sleep(0.3)
    finally:
        sessao.fechar()
    return 0


def main(argv: list[str] | None = None) -> int:
    faulthandler.enable(all_threads=True)
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    ensure_cuda_libraries()
    args = parse_args(argv)
    from pathlib import Path

    args.log_dir_path = Path(args.log_dir)
    if not args.sem_pid:
        write_pid()
    try:
        if args.sem_interface:
            return run_headless(args)
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            print("A interface só sobe em 127.0.0.1.", flush=True)
            return 1
        sessao = Sessao(args.log_dir_path, janela=args.janela, sobreposicao=args.sobreposicao)

        def request_stop(_signum=None, _frame=None) -> None:
            sessao.fechar()
            raise SystemExit(0)

        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
            if sig is None:
                continue
            try:
                signal.signal(sig, request_stop)
            except (ValueError, OSError):
                pass
        try:
            serve(args.host, args.porta, sessao)
        except OSError as exc:
            print(f"Não abri a interface na porta {args.porta}: {exc}", flush=True)
            return 1
        return 0
    finally:
        if not args.sem_pid:
            remove_pid()


if __name__ == "__main__":
    sys.exit(main())
