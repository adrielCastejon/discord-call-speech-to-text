"""Servidor local da interface. Escuta só em 127.0.0.1."""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from discord_stt.paths import STOP_FILE, WEB
from discord_stt.session import Sessao

mimetypes.add_type("text/javascript", ".js")


class InterfaceServer(ThreadingHTTPServer):
    sessao: Sessao
    daemon_threads = True


def make_handler(sessao: Sessao):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/estado":
                desde = 0
                raw = parse_qs(parsed.query).get("desde", ["0"])[0]
                try:
                    desde = max(0, int(raw))
                except ValueError:
                    desde = 0
                self._json(200, sessao.foto(desde))
                return
            if parsed.path in {"/", "/index.html"}:
                self._file(WEB / "index.html")
                return
            if parsed.path in {"/app.js", "/style.css"}:
                self._file(WEB / parsed.path.lstrip("/"))
                return
            self._json(404, {"erro": "não encontrado"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            body = self._body()
            routes = {
                "/api/iniciar": lambda: sessao.iniciar(str(body.get("modelo", "small")), str(body.get("idioma", "pt"))),
                "/api/pausar": sessao.pausar,
                "/api/continuar": sessao.continuar,
                "/api/encerrar": sessao.encerrar,
            }
            action = routes.get(parsed.path)
            if action is None:
                self._json(404, {"erro": "não encontrado"})
                return
            ok, message = action()
            payload = sessao.foto(0)
            payload["mensagem"] = message
            self._json(200 if ok else 409, payload)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(min(length, 100_000))
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        def _json(self, code: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path) -> None:
            if not path.is_file():
                self._json(404, {"erro": "não encontrado"})
                return
            data = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def watch_stop_file(httpd: InterfaceServer, sessao: Sessao) -> None:
    while True:
        if STOP_FILE.exists():
            try:
                STOP_FILE.unlink()
            except OSError:
                pass
            sessao.fechar()
            httpd.shutdown()
            return
        time.sleep(0.3)


def serve(host: str, porta: int, sessao: Sessao) -> None:
    httpd = InterfaceServer((host, porta), make_handler(sessao))
    httpd.sessao = sessao
    threading.Thread(target=watch_stop_file, args=(httpd, sessao), name="parar", daemon=True).start()
    print(f"Interface: http://{host}:{porta}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        sessao.fechar()
        httpd.server_close()
