"""`heimdall serve`: a local HTTP service that loads one model once (Spec 0005 B1-B9).

Standard library only. No authentication, so it only binds loopback addresses.
A guard is any object with `threshold`, `model_version` and `score_windows(text) -> (score, n_windows, truncated)`.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from heimdall import keyword

MAX_BODY = 1024 * 1024  # 1 MiB
READ_TIMEOUT = 10.0  # B18: seconds a connection may stall while the server reads from it
_DIGITS = re.compile(r"[0-9]+")  # B7: int() would also accept "+12", "1_2", "12 " and non-ASCII digits


class KeywordGuard:
    """The keyword baseline behind the same interface as EncoderGuard."""

    threshold = 0.5
    model_version = keyword.MODEL_VERSION

    def score_windows(self, text: str):
        return keyword.score(text), 1, False


def check_host(host: str) -> None:
    """B2: only loopback addresses; the service has no authentication."""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError(f"--host {host} is not a loopback address; this service has no authentication, "
                     "use 127.0.0.1, ::1 or localhost")


def load_guard(model: str, encoder_dir: Path, force_cpu: bool):
    if model == "keyword":
        return KeywordGuard()
    from heimdall.encoder import EncoderGuard

    return EncoderGuard(Path(encoder_dir), force_cpu=force_cpu)


def classify(guard, lock: threading.Lock, text: str) -> dict:
    """B4 and B6: one verdict; empty text is safe without calling the model."""
    if not text.strip():
        score, n_windows, truncated, latency_ms = 0.0, 0, False, 0.0
    else:
        with lock:
            t0 = time.perf_counter()
            score, n_windows, truncated = guard.score_windows(text)
            latency_ms = (time.perf_counter() - t0) * 1000
    return {"label": "injection" if score >= guard.threshold else "safe", "score": float(score),
            "model_version": guard.model_version, "threshold": float(guard.threshold),
            "latency_ms": round(latency_ms, 2), "n_windows": n_windows, "truncated": truncated}


def make_handler(guard, read_timeout: float = READ_TIMEOUT):
    lock = threading.Lock()  # B8: one model, one inference at a time

    class Handler(BaseHTTPRequestHandler):
        server_version = "heimdall"
        timeout = read_timeout  # B18: socket timeout; also closes idle keep-alive connections

        def _send(self, status: int, body: dict) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path != "/health":
                return self._send(404, {"error": f"not found: GET {self.path}"})
            self._send(200, {"status": "ok", "model_version": guard.model_version,
                             "threshold": float(guard.threshold)})

        def do_POST(self):
            if self.path != "/classify":
                self.close_connection = True  # the body is not read
                return self._send(404, {"error": f"not found: POST {self.path}"})
            raw_length = self.headers.get("Content-Length")
            if raw_length is None or not _DIGITS.fullmatch(raw_length):
                self.close_connection = True
                return self._send(400, {"error": "Content-Length must be a non-negative decimal integer"})
            length = int(raw_length)
            if length > MAX_BODY:
                self.close_connection = True
                return self._send(413, {"error": f"body is larger than {MAX_BODY} bytes"})
            try:
                raw = self.rfile.read(length)
            except TimeoutError:  # B18: the client sent fewer bytes than Content-Length
                self.close_connection = True
                return self._send(408, {"error": f"body not received within {self.timeout:g} s"})
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, RecursionError):  # ValueError includes UnicodeDecodeError and JSONDecodeError
                return self._send(400, {"error": "body must be UTF-8 JSON"})
            if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                return self._send(400, {"error": 'body must be a JSON object with a string "text"'})
            try:
                verdict = classify(guard, lock, body["text"])
            except Exception as err:  # B17: one line on stderr, no traceback in the response
                print(f"heimdall serve: internal error on POST /classify: {type(err).__name__}: {err}",
                      file=sys.stderr, flush=True)
                return self._send(500, {"error": f"internal error: {type(err).__name__}"})
            self._send(200, verdict)

    return Handler


def make_server(guard, host: str = "127.0.0.1", port: int = 8765,
                read_timeout: float = READ_TIMEOUT) -> ThreadingHTTPServer:
    check_host(host)
    cls = ThreadingHTTPServer
    if ":" in host:
        cls = type("ThreadingHTTPServerV6", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6})
    return cls((host, port), make_handler(guard, read_timeout))


def run(host: str, port: int, encoder_dir: Path, model: str, force_cpu: bool) -> int:
    try:
        check_host(host)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    try:
        guard = load_guard(model, encoder_dir, force_cpu)
    except FileNotFoundError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        server = make_server(guard, host, port)
    except OSError as err:  # e.g. the port is already in use
        print(f"error: cannot listen on {host}:{port}: {err}", file=sys.stderr)
        return 1
    shown = f"[{host}]" if ":" in host else host
    print(f"heimdall serve: listening on http://{shown}:{server.server_address[1]} model_version={guard.model_version} "
          f"threshold={guard.threshold:.4f}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
