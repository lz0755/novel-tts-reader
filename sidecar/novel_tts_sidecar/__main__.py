"""One control process, one supervised spawn child, no model imports in this process."""

import argparse
import hashlib
import hmac
import json
import multiprocessing as mp
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


from .worker import worker


def monitor_parent(server):
    if os.name == "nt" and not sys.stdin.isatty():
        import ctypes
        import msvcrt
        from ctypes import wintypes

        # A pending synchronous ReadFile can block the Windows spawn path when it
        # inspects the same standard handle. Only drain bytes known to be ready.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel.PeekNamedPipe
        peek.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
            wintypes.LPDWORD,
        ]
        peek.restype = wintypes.BOOL
        fd = sys.stdin.fileno()
        handle = msvcrt.get_osfhandle(fd)
        available = wintypes.DWORD()
        while not server.control.stop.wait(0.1):
            if not peek(handle, None, 0, None, ctypes.byref(available), None):
                break
            if available.value:
                try:
                    if not os.read(fd, min(available.value, 4096)):
                        break
                except OSError:
                    break
        else:
            return
    else:
        sys.stdin.read()
    server.shutdown()


class Controller:
    def __init__(self, folder, engine, settings, timeout):
        self.folder, self.engine, self.settings, self.timeout = (
            folder,
            engine,
            settings,
            timeout,
        )
        self.context = mp.get_context("spawn")
        self.lock = threading.RLock()
        self.proc = self.pipe = self.active = None
        self.jobs = {}
        self.caps = None
        self.stop = threading.Event()
        self.monitor = threading.Thread(target=self.poll, daemon=True)
        self.monitor.start()

    def spawn(self):
        parent, child = self.context.Pipe()
        self.proc = self.context.Process(
            target=worker, args=(child, self.folder, self.engine, self.settings)
        )
        self.proc.start()
        child.close()
        self.pipe = parent

    def reap(self):
        if self.proc:
            self.proc.terminate()
            self.proc.join(1)
            if self.proc.is_alive():
                self.proc.kill()
                self.proc.join(3)
            if self.proc.is_alive():
                raise RuntimeError("worker_reap_failed")
            self.proc.close()
            self.proc = None
            self.pipe.close()
            self.pipe = None
        self.caps = None

    def submit(self, kind, data):
        with self.lock:
            if self.stop.is_set():
                return 503, {"error": "closing"}
            job = data["job_id"]
            signature = hashlib.sha256(
                json.dumps([kind, data], sort_keys=True).encode()
            ).hexdigest()
            if job in self.jobs:
                old = self.jobs[job]
                return (
                    (200, {"id": job, "state": old["state"]})
                    if old["hash"] == signature
                    else (409, {"error": "job_id_conflict"})
                )
            if self.active:
                return 409, {"error": "busy"}
            if kind == "load":
                self.reap()
                self.spawn()
            elif not self.caps or data.get("model") != self.caps["model"]:
                return 409, {"error": "model_not_ready"}
            if len(self.jobs) >= 256:
                oldest = next(iter(self.jobs))
                self.jobs.pop(oldest)
                (Path(self.folder) / (oldest + ".wav")).unlink(missing_ok=True)
            self.jobs[job] = {
                "id": job,
                "kind": kind,
                "state": "running",
                "hash": signature,
            }
            self.active = job
            self.deadline = time.monotonic() + self.timeout
            self.pipe.send((kind, job, data))
            return 202, {"id": job, "state": "running"}

    def abort(self, reason):
        if self.active:
            self.jobs[self.active].update(state="interrupted", error=reason)
            self.active = None
        self.reap()

    def poll(self):
        while not self.stop.wait(0.025):
            with self.lock:
                if not self.active:
                    if self.proc and not self.proc.is_alive():
                        self.reap()
                    continue
                job = self.jobs[self.active]
                try:
                    if self.pipe.poll():
                        ident, state, result = self.pipe.recv()
                        if ident != self.active:
                            raise ValueError("unmatched worker reply")
                        if job["state"] == "cancel_requested":
                            state, result = "cancelled", {}
                        job.update(state=state, result=result)
                        if job["kind"] == "load" and state == "succeeded":
                            self.caps = result
                        self.active = None
                        if state == "failed":
                            self.reap()
                    elif not self.proc.is_alive():
                        self.abort("worker_exit")
                    elif time.monotonic() > self.deadline:
                        self.abort("worker_timeout")
                except (EOFError, OSError, ValueError):
                    self.abort("worker_exit")

    def health(self):
        with self.lock:
            return dict(
                version=1,
                control="alive",
                pid=self.proc.pid if self.proc else None,
                model_ready=bool(self.caps),
                busy=bool(self.active),
                accepting=not self.stop.is_set(),
            )

    def close(self):
        self.stop.set()
        with self.lock:
            self.abort("shutdown")
        self.monitor.join(2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data):
        raw = isinstance(data, bytes)
        body = data if raw else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "audio/wav" if raw else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        credential = self.headers.get("Authorization", "")
        if not hmac.compare_digest(credential, "Bearer " + self.server.token):
            self.reply(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        c = self.server.control
        if self.path == "/v1/health":
            return self.reply(200, c.health())
        if self.path == "/v1/capabilities":
            return self.reply(200, c.caps or {"model": None})
        match = re.fullmatch(r"/v1/(jobs|artifacts)/([0-9a-f]{32})", self.path)
        if match:
            with c.lock:
                kind, ident = match.groups()
                job = c.jobs.get(ident)
                if job and kind == "jobs":
                    return self.reply(
                        200, {k: v for k, v in job.items() if k != "hash"}
                    )
                if job and job["state"] == "succeeded" and job["kind"] == "synthesis":
                    data = (Path(c.folder) / (ident + ".wav")).read_bytes()
                else:
                    data = None
            if data:
                return self.reply(200, data)
        self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if not self.authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 32768:
                raise ValueError()
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            return self.reply(400, {"error": "invalid_json"})
        c = self.server.control
        if self.path == "/v1/shutdown":
            self.reply(200, {"state": "stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        match = re.fullmatch(r"/v1/jobs/([0-9a-f]{32})/cancel", self.path)
        if match:
            with c.lock:
                if set(data) - {"hard"} or type(data.get("hard", False)) is not bool:
                    return self.reply(400, {"error": "invalid_cancel"})
                if match[1] != c.active:
                    return self.reply(409, {"error": "not_active"})
                if data.get("hard"):
                    c.abort("hard_cancel")
                else:
                    c.jobs[match[1]]["state"] = "cancel_requested"
            return self.reply(
                200,
                {"state": "interrupted" if data.get("hard") else "cancel_requested"},
            )
        if self.path not in ("/v1/models/load", "/v1/synthesis/jobs"):
            return self.reply(404, {"error": "not_found"})
        kind = "load" if self.path.endswith("/load") else "synthesis"
        required = (
            {"job_id", "model"}
            if kind == "load"
            else {"job_id", "model", "text", "voice"}
        )
        extra = {"style", "scenario"} if kind == "synthesis" else set()
        if (
            not required <= set(data)
            or set(data) - required - extra
            or any(type(v) is not str for v in data.values())
            or not re.fullmatch(r"[0-9a-f]{32}", data["job_id"])
            or not data["model"]
            or len(data.get("text", "")) > 4000
        ):
            return self.reply(400, {"error": "invalid_request"})
        code, body = c.submit(kind, data)
        self.reply(code, body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["mock", "qwen"], default="mock")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args()
    token = sys.stdin.readline().strip()
    if len(token) < 32:
        raise SystemExit("missing session token")
    settings = (
        json.loads(args.settings.read_text(encoding="utf-8")) if args.settings else {}
    )
    with (
        tempfile.TemporaryDirectory(prefix="novel-worker-") as folder,
        ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server,
    ):
        server.token = token
        server.control = Controller(folder, args.engine, settings, args.timeout)
        print(
            json.dumps(
                dict(
                    version=1,
                    host="127.0.0.1",
                    port=server.server_port,
                    pid=os.getpid(),
                )
            ),
            flush=True,
        )

        threading.Thread(target=monitor_parent, args=(server,), daemon=True).start()
        try:
            server.serve_forever(poll_interval=0.05)
        finally:
            server.control.close()


if __name__ == "__main__":
    mp.freeze_support()
    main()
