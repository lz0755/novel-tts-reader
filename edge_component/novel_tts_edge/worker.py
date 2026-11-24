"""Stdio JSON-lines worker for Edge TTS synthesis and voice catalog queries."""

from __future__ import annotations

import asyncio
import email.utils
import json
import sys
import threading
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

import aiohttp
import edge_tts
from edge_tts.drm import DRM
from edge_tts.exceptions import (
    NoAudioReceived,
    SkewAdjustmentError,
    UnexpectedResponse,
    UnknownResponse,
    WebSocketError,
)

from novel_tts_edge import PROTOCOL_VERSION
from novel_tts_edge.mp3 import MP3ValidationError, decode_mp3_to_wav

DEFAULT_MAX_MP3_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_PCM_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_DURATION_SECONDS = 600


class UpstreamStatusError(Exception):
    def __init__(
        self,
        status: int,
        message: str = "",
        retry_after: float | None = None,
        attempts_used: int = 1,
    ):
        super().__init__(message or f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after
        self.attempts_used = attempts_used


class EdgeBackend(Protocol):
    async def fetch_mp3(
        self,
        *,
        text: str,
        voice: str,
        rate: str,
        pitch: str,
        volume: str,
        connect_timeout: float,
        receive_timeout: float,
        max_mp3_bytes: int,
        mark_sent: Any,
        mark_attempt: Any,
    ) -> bytes: ...

    async def list_voices(
        self, *, connect_timeout: float, receive_timeout: float
    ) -> list[dict[str, Any]]: ...


def _parse_retry_after(headers: Any) -> float | None:
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
        return max(0.0, value)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


class RealEdgeBackend:
    """Default backend backed by edge-tts 7.3.1."""

    async def fetch_mp3(
        self,
        *,
        text: str,
        voice: str,
        rate: str,
        pitch: str,
        volume: str,
        connect_timeout: float,
        receive_timeout: float,
        max_mp3_bytes: int,
        mark_sent: Any,
        mark_attempt: Any,
    ) -> bytes:
        comm = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
            connect_timeout=max(1, int(round(connect_timeout))),
            receive_timeout=max(1, int(round(receive_timeout))),
        )
        orig_handler = DRM.handle_client_response_error
        synth_task = asyncio.current_task()

        def tracked_handler(exc: aiohttp.ClientResponseError) -> None:
            orig_handler(exc)
            if asyncio.current_task() is synth_task:
                mark_attempt()

        chunks = bytearray()
        mark_sent()
        DRM.handle_client_response_error = staticmethod(tracked_handler)
        try:
            async for item in comm.stream():
                if item.get("type") == "audio":
                    data = item.get("data") or b""
                    if len(chunks) + len(data) > max_mp3_bytes:
                        raise MP3ValidationError("size_limit", "下载的 MP3 超过单段大小上限")
                    chunks.extend(data)
        finally:
            DRM.handle_client_response_error = staticmethod(orig_handler)

        # edge_tts resets chunk_audio_bytes to 0 only when turn.end is received.
        if comm.state.get("chunk_audio_bytes", 0) != 0:
            raise MP3ValidationError("truncated_audio", "音频流在传输完成前中断")
        if not chunks:
            raise MP3ValidationError("empty_audio", "未收到有效音频数据")
        return bytes(chunks)

    async def list_voices(
        self, *, connect_timeout: float, receive_timeout: float
    ) -> list[dict[str, Any]]:
        timeout = max(1.0, float(connect_timeout) + float(receive_timeout))
        raw = await asyncio.wait_for(edge_tts.list_voices(), timeout=timeout)
        voices: list[dict[str, Any]] = []
        for entry in raw:
            short = str(entry.get("ShortName") or "").strip()
            if not short:
                continue
            voices.append(
                {
                    "short_name": short,
                    "locale": str(entry.get("Locale") or "").strip(),
                    "gender": str(entry.get("Gender") or "").strip(),
                    "friendly_name": str(entry.get("FriendlyName") or "").strip(),
                }
            )
        return voices


def _classify_error(exc: BaseException, sent: bool, attempts_used: int) -> dict[str, Any]:
    if isinstance(exc, asyncio.CancelledError):
        return {
            "error_code": "cancelled",
            "message": "已取消",
            "retryable": False,
            "sent": sent,
            "attempts_used": attempts_used,
        }
    if isinstance(exc, MP3ValidationError):
        return {
            "error_code": exc.code,
            "message": exc.message,
            "retryable": exc.code == "truncated_audio",
            "sent": sent,
            "attempts_used": attempts_used,
        }
    if isinstance(exc, UpstreamStatusError):
        status = exc.status
        if status == 403:
            return {
                "error_code": "forbidden_403",
                "message": "微软在线语音服务拒绝访问（HTTP 403），服务当前不可用",
                "retryable": False,
                "http_status": 403,
                "sent": sent,
                "attempts_used": max(attempts_used, exc.attempts_used),
            }
        if status == 429:
            return {
                "error_code": "rate_limited_429",
                "message": "微软在线语音服务请求过多（HTTP 429）",
                "retryable": True,
                "http_status": 429,
                "retry_after": exc.retry_after,
                "sent": sent,
                "attempts_used": max(attempts_used, exc.attempts_used),
            }
        retryable = 500 <= status < 600
        return {
            "error_code": f"http_{status}",
            "message": f"微软在线语音服务返回 HTTP {status}",
            "retryable": retryable,
            "http_status": status,
            "sent": sent,
            "attempts_used": max(attempts_used, exc.attempts_used),
        }
    if isinstance(exc, aiohttp.ClientResponseError):
        status = int(exc.status)
        if status == 403:
            return {
                "error_code": "forbidden_403",
                "message": "微软在线语音服务拒绝访问（HTTP 403），服务当前不可用",
                "retryable": False,
                "http_status": 403,
                "sent": sent,
                "attempts_used": attempts_used,
            }
        if status == 429:
            return {
                "error_code": "rate_limited_429",
                "message": "微软在线语音服务请求过多（HTTP 429）",
                "retryable": True,
                "http_status": 429,
                "retry_after": _parse_retry_after(exc.headers),
                "sent": sent,
                "attempts_used": attempts_used,
            }
        return {
            "error_code": f"http_{status}",
            "message": f"微软在线语音服务返回 HTTP {status}",
            "retryable": 500 <= status < 600,
            "http_status": status,
            "sent": sent,
            "attempts_used": attempts_used,
        }
    if isinstance(exc, SkewAdjustmentError):
        return {
            "error_code": "forbidden_403",
            "message": "微软在线语音服务拒绝访问（HTTP 403，时钟校准失败），服务当前不可用",
            "retryable": False,
            "http_status": 403,
            "sent": sent,
            "attempts_used": attempts_used,
        }
    if isinstance(exc, NoAudioReceived):
        return {
            "error_code": "no_audio_received",
            "message": "微软在线语音服务未返回任何音频，请确认音色可用且正文含可朗读文字",
            "retryable": False,
            "sent": sent,
            "attempts_used": attempts_used,
        }
    if isinstance(exc, (aiohttp.ConnectionTimeoutError,)):
        return {
            "error_code": "connect_timeout",
            "message": "连接微软在线语音服务超时",
            "retryable": True,
            "sent": sent,
            "attempts_used": attempts_used,
        }
    if isinstance(exc, (aiohttp.ServerTimeoutError, asyncio.TimeoutError, TimeoutError)):
        return {
            "error_code": "read_timeout",
            "message": "等待微软在线语音响应超时",
            "retryable": True,
            "sent": sent,
            "attempts_used": attempts_used,
        }
    if isinstance(
        exc,
        (
            aiohttp.ClientError,
            WebSocketError,
            UnexpectedResponse,
            UnknownResponse,
            OSError,
        ),
    ):
        return {
            "error_code": "network_error",
            "message": f"Edge 在线语音通信异常（{type(exc).__name__}）",
            "retryable": True,
            "sent": sent,
            "attempts_used": attempts_used,
        }
    return {
        "error_code": "internal_error",
        "message": f"Edge 组件内部错误（{type(exc).__name__}）",
        "retryable": False,
        "sent": sent,
        "attempts_used": attempts_used,
    }


def _pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


class WorkerSession:
    def __init__(self, backend: EdgeBackend, stdout=None):
        self.backend = backend
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._tasks: dict[str, tuple[asyncio.Task[Any], threading.Event]] = {}
        self._synth_active: str | None = None
        self._shutdown = asyncio.Event()

    def send(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            self.stdout.write(line)
            self.stdout.flush()

    @staticmethod
    def _cancel(task: asyncio.Task, cancel_event: threading.Event) -> None:
        if not cancel_event.is_set():
            cancel_event.set()
            # A just-created task must enter its cleanup/response handler first.
            asyncio.get_running_loop().call_soon(task.cancel)

    def handle_command(self, message: dict[str, Any]) -> None:
        cmd = message.get("cmd")
        if cmd == "shutdown":
            self.cancel_all()
            self._shutdown.set()
            return
        if cmd == "cancel":
            job_id = str(message.get("job_id") or "")
            entry = self._tasks.get(job_id)
            if entry is not None:
                self._cancel(*entry)
            return
        if cmd == "synthesize":
            job_id = str(message.get("job_id") or "")
            if not job_id:
                return
            if self._synth_active is not None:
                self.send(
                    {
                        "type": "result",
                        "job_id": job_id,
                        "ok": False,
                        "error_code": "busy",
                        "message": "Edge 组件正忙",
                        "retryable": True,
                        "sent": False,
                        "attempts_used": 0,
                        "elapsed_ms": 0,
                    }
                )
                return
            cancel_event = threading.Event()
            task = asyncio.create_task(self._run_synthesize(job_id, message, cancel_event))
            self._synth_active = job_id
            self._tasks[job_id] = (task, cancel_event)
            return
        if cmd == "list_voices":
            job_id = str(message.get("job_id") or "")
            if not job_id:
                return
            cancel_event = threading.Event()
            task = asyncio.create_task(self._run_list_voices(job_id, message, cancel_event))
            self._tasks[job_id] = (task, cancel_event)
            return

    def cancel_all(self) -> None:
        for entry in self._tasks.values():
            self._cancel(*entry)

    async def _run_synthesize(
        self, job_id: str, message: dict[str, Any], cancel_event: threading.Event
    ) -> None:
        started = time.monotonic()
        sent = False
        attempts_used = 1

        def mark_sent() -> None:
            nonlocal sent
            sent = True

        def mark_attempt() -> None:
            nonlocal attempts_used
            if attempts_used >= max_attempts:
                raise UpstreamStatusError(403, attempts_used=attempts_used)
            attempts_used += 1

        destination: Path | None = None
        try:
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            text = str(message.get("text") or "")
            voice = str(message.get("voice") or "").strip()
            rate = str(message.get("rate") or "+0%")
            pitch = str(message.get("pitch") or "+0Hz")
            volume = str(message.get("volume") or "+0%")
            dest_raw = str(message.get("destination") or "")
            if not text.strip() or not voice or not dest_raw.endswith(".partial"):
                self.send(
                    {
                        "type": "result",
                        "job_id": job_id,
                        "ok": False,
                        "error_code": "invalid_request",
                        "message": "合成参数无效",
                        "retryable": False,
                        "sent": False,
                        "attempts_used": 0,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                )
                return
            destination = Path(dest_raw)
            connect_timeout = float(message.get("connect_timeout", 10.0))
            receive_timeout = float(message.get("receive_timeout", 15.0))
            max_attempts = int(message.get("max_attempts", 2))
            max_mp3_bytes = int(message.get("max_mp3_bytes", DEFAULT_MAX_MP3_BYTES))
            max_pcm_bytes = int(message.get("max_pcm_bytes", DEFAULT_MAX_PCM_BYTES))
            max_duration = int(
                message.get("max_duration_seconds", DEFAULT_MAX_DURATION_SECONDS)
            )

            mp3_bytes = await self.backend.fetch_mp3(
                text=text,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
                connect_timeout=connect_timeout,
                receive_timeout=receive_timeout,
                max_mp3_bytes=max_mp3_bytes,
                mark_sent=mark_sent,
                mark_attempt=mark_attempt,
            )
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            decode = asyncio.create_task(
                asyncio.to_thread(
                    decode_mp3_to_wav,
                    mp3_bytes,
                    destination,
                    max_mp3_bytes=max_mp3_bytes,
                    max_pcm_bytes=max_pcm_bytes,
                    max_duration_seconds=max_duration,
                    cancel_check=cancel_event.is_set,
                )
            )
            try:
                frames, sr, channels, width = await asyncio.shield(decode)
            except asyncio.CancelledError:
                # Cancelling an asyncio task does not stop the decoder thread.
                cancel_event.set()
                await asyncio.gather(decode, return_exceptions=True)
                raise
            if cancel_event.is_set():
                destination.unlink(missing_ok=True)
                raise asyncio.CancelledError()
            self.send(
                {
                    "type": "result",
                    "job_id": job_id,
                    "ok": True,
                    "frames": frames,
                    "rate": sr,
                    "channels": channels,
                    "width": width,
                    "mp3_bytes": len(mp3_bytes),
                    "attempts_used": attempts_used,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
        except BaseException as exc:
            if destination is not None:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            info = _classify_error(exc, sent, attempts_used)
            self.send(
                {
                    "type": "result",
                    "job_id": job_id,
                    "ok": False,
                    **info,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
        finally:
            if self._synth_active == job_id:
                self._synth_active = None
            self._tasks.pop(job_id, None)

    async def _run_list_voices(
        self, job_id: str, message: dict[str, Any], cancel_event: threading.Event
    ) -> None:
        started = time.monotonic()
        try:
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            connect_timeout = float(message.get("connect_timeout", 10.0))
            receive_timeout = float(message.get("receive_timeout", 15.0))
            voices = await self.backend.list_voices(
                connect_timeout=connect_timeout, receive_timeout=receive_timeout
            )
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            self.send(
                {
                    "type": "result",
                    "job_id": job_id,
                    "ok": True,
                    "voices": voices,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            info = _classify_error(exc, False, 1)
            self.send(
                {
                    "type": "result",
                    "job_id": job_id,
                    "ok": False,
                    **info,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
        finally:
            self._tasks.pop(job_id, None)

    async def wait_shutdown(self) -> None:
        await self._shutdown.wait()
        tasks = [task for task, _ in self._tasks.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def _serve(backend: EdgeBackend, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    loop = asyncio.get_running_loop()
    session = WorkerSession(backend, stdout=stdout)
    session.send(
        {
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "edge_tts_version": _pkg_version("edge-tts"),
            "miniaudio_version": _pkg_version("miniaudio"),
        }
    )

    def reader() -> None:
        try:
            for raw_line in stdin:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    loop.call_soon_threadsafe(session.handle_command, msg)
        finally:
            loop.call_soon_threadsafe(
                session.handle_command, {"cmd": "shutdown"}
            )

    thread = threading.Thread(target=reader, name="edge-stdin-reader", daemon=True)
    thread.start()
    await session.wait_shutdown()
    return 0


def run_worker(backend: EdgeBackend | None = None) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(_serve(backend or RealEdgeBackend()))


def main() -> int:
    return run_worker()
