"""One control process, one supervised spawn child, no model imports in this process."""

import argparse
import hashlib
import hmac
import json
import multiprocessing as mp
import os
import re
import secrets
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
        self.cancel_event = self.context.Event()
        self.proc = self.context.Process(
            target=worker, args=(child, self.folder, self.engine, self.settings, self.cancel_event)
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
            elif "stream_session" in data and data["stream_session"] != self.caps.get("stream_session"):
                return 409, {"error": "stream_session_changed"}
            elif not data["text"].strip() or data["voice"] not in self.caps["voices"]:
                return 400, {"error": "invalid_voice_or_text"}
            if len(self.jobs) >= 256:
                oldest = next(iter(self.jobs))
                self.jobs.pop(oldest)
                self.discard_audio(oldest)
            self.jobs[job] = {
                "id": job,
                "kind": kind,
                "state": "running",
                "hash": signature,
            }
            self.active = job
            self.cancel_event.clear()
            if "stream_session" in data:
                self.jobs[job]["pcm_frames"] = 0
            self.deadline = time.monotonic() + self.timeout
            self.pipe.send((kind, job, data))
            return 202, {"id": job, "state": "running"}

    def discard_audio(self, ident):
        try:
            (Path(self.folder) / (ident + ".wav")).unlink(missing_ok=True)
        except PermissionError:
            # TemporaryDirectory retries cleanup when the control service exits.
            pass

    def abort(self, reason):
        self.reap()
        if self.active:
            self.jobs[self.active].update(state="interrupted", error=reason)
            self.discard_audio(self.active)
            self.active = None

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
                        if state == "pcm":
                            if time.monotonic() > self.deadline:
                                self.abort("worker_timeout")
                            elif job["state"] == "running":
                                job["pcm_frames"] = result
                            continue
                        if job["state"] == "cancel_requested":
                            state, result = "cancelled", {}
                        if job["kind"] == "load" and state == "succeeded":
                            if result.get("streaming"):
                                result["stream_session"] = secrets.token_hex(16)
                            self.caps = result
                        job.update(state=state, result=result)
                        self.active = None
                        if state != "succeeded":
                            self.discard_audio(ident)
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

    def reply(self, code, data, content_type=None):
        raw = isinstance(data, bytes)
        body = data if raw else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type or ("audio/wav" if raw else "application/json"))
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
        pcm = re.fullmatch(r"/v1/jobs/([0-9a-f]{32})/pcm/(\d{1,9})", self.path)
        if pcm:
            with c.lock:
                job = c.jobs.get(pcm[1], {})
                offset = int(pcm[2])
                count = job.get("pcm_frames", 0) - offset
                if job.get("state") not in ("running", "succeeded") or count <= 0:
                    data = None
                else:
                    # The worker writes a canonical PCM16 mono WAV unbuffered.
                    # Read only frames it has published, independent of the provisional header.
                    with (Path(c.folder) / (pcm[1] + ".wav")).open("rb") as wav:
                        wav.seek(44 + 2 * offset)
                        data = wav.read(2 * min(count, 65536))
            if data:
                return self.reply(200, data, "application/octet-stream")
            return self.reply(404, {"error": "pcm_unavailable"})
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
                    c.cancel_event.set()
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
        extra = {"style", "scenario", "stream_session"} if kind == "synthesis" else {"quantization"}
        if (
            not required <= set(data)
            or set(data) - required - extra
            or any(type(v) is not str for v in data.values())
            or not re.fullmatch(r"[0-9a-f]{32}", data["job_id"])
            or not data["model"]
            or len(data.get("text", "")) > 4000
            or ("quantization" in data and (
                c.engine != "qwen" or data["quantization"] not in ("none", "int4")
            ))
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
    with (
        tempfile.TemporaryDirectory(prefix="novel-worker-") as folder,
        ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server,
    ):
        server.token = token
        server.control = Controller(folder, args.engine, args.settings, args.timeout)
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
