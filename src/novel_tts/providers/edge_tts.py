"""Edge TTS provider, chapter-scoped authorization, voice catalog, and stdio runtime bridge."""

from __future__ import annotations

import heapq
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from novel_tts.domain.models import (
    AudioArtifact,
    Cancelled,
    Capabilities,
    ProviderError,
    SynthesisRequest,
)
from novel_tts.domain.voices import (
    EDGE_BUILTIN_CATALOG,
    EDGE_DEFAULT_NARRATOR,
    EDGE_PRESETS,
)
from novel_tts.infrastructure.audio import inspect_wav

EDGE_PROVIDER = "edge_tts"
EDGE_MODEL = "edge-read-aloud"
EDGE_MODEL_REVISION = "edge-read-aloud-alias"
EDGE_PROTOCOL = "edge-stdio-v1"
EDGE_PROTOCOL_VERSION = 1
EDGE_INPUT_LIMIT = 600

MAX_MP3_BYTES = 32 * 1024 * 1024
MAX_PCM_BYTES = 32 * 1024 * 1024
MAX_DURATION_SECONDS = 600

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_RECEIVE_TIMEOUT = 15.0
DEFAULT_JOB_TIMEOUT = 60.0
DEFAULT_MAX_ATTEMPTS = 3

PRIORITY_PLAYBACK = 0
PRIORITY_PREGEN = 1
PRIORITY_PREFETCH = 2

_CUSTOM_VOICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
_priority_local = threading.local()


class EdgeAuthorizationRequired(ProviderError):
    def __init__(self, code: str = "当前章节尚未允许发送到微软合成（仅用缓存）"):
        super().__init__(code, retryable=False, outcome="known")


class EdgeComponentError(ProviderError):
    def __init__(self, code: str):
        super().__init__(code, retryable=False, outcome="known")


class EdgeVoiceUnavailableError(ProviderError):
    def __init__(self, voice: str):
        super().__init__(
            f"音色「{voice}」已不在当前可用列表中，请为该角色选择替代音色后再生成",
            retryable=False,
            outcome="known",
        )


@contextmanager
def edge_priority(level: int):
    previous = getattr(_priority_local, "level", None)
    _priority_local.level = level
    try:
        yield
    finally:
        if previous is None:
            if hasattr(_priority_local, "level"):
                delattr(_priority_local, "level")
        else:
            _priority_local.level = previous


def current_edge_priority(default: int = PRIORITY_PREGEN) -> int:
    return getattr(_priority_local, "level", default)


def _wait_either(cancel, revoked: threading.Event, timeout: float) -> str:
    """Wait up to timeout seconds; return 'cancel', 'revoked', or 'timeout'."""
    end = time.monotonic() + max(0.0, timeout)
    while True:
        if cancel.is_set():
            return "cancel"
        if revoked.is_set():
            return "revoked"
        remaining = end - time.monotonic()
        if remaining <= 0:
            return "timeout"
        step = min(0.05, remaining)
        if cancel.wait(step):
            return "cancel"


def _sort_catalog_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    preset_order = {name.lower(): idx for idx, name in enumerate(EDGE_PRESETS)}

    def sort_key(item: dict[str, str]):
        short = item["short_name"]
        low = short.lower()
        if low in preset_order:
            return (0, preset_order[low], short)
        locale = (item.get("locale") or "").lower()
        if locale.startswith("zh-cn"):
            return (1, 0, short)
        if locale.startswith("zh-"):
            return (2, locale, short)
        return (3, locale, short)

    return sorted(entries, key=sort_key)


class EdgeVoiceCatalog:
    """Persists Edge TTS voice metadata, custom voice IDs, and default narrator."""

    def __init__(self, root: Path):
        self.path = Path(root) / "edge_voices.json"
        self.lock = threading.RLock()
        self._state = self._load()

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "refreshed": False,
            "refreshed_at": "",
            "voices": [dict(v) for v in EDGE_BUILTIN_CATALOG],
            "custom_voices": [],
            "narrator_voice": EDGE_DEFAULT_NARRATOR,
        }

    def _load(self) -> dict[str, Any]:
        default = self._default_state()
        if not self.path.is_file():
            return default
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return default
        except (OSError, ValueError):
            return default
        voices = []
        seen = set()
        for item in raw.get("voices") or ():
            if not isinstance(item, dict):
                continue
            short = str(item.get("short_name") or "").strip()
            if not short or short.lower() in seen:
                continue
            seen.add(short.lower())
            voices.append(
                {
                    "short_name": short,
                    "locale": str(item.get("locale") or "").strip(),
                    "gender": str(item.get("gender") or "").strip(),
                    "friendly_name": str(item.get("friendly_name") or "").strip(),
                }
            )
        if not voices:
            voices = [dict(v) for v in EDGE_BUILTIN_CATALOG]
        custom = []
        custom_seen = set()
        for item in raw.get("custom_voices") or ():
            v = str(item or "").strip()
            if v and _CUSTOM_VOICE_RE.match(v) and v.lower() not in custom_seen:
                custom_seen.add(v.lower())
                custom.append(v)
        narrator = str(raw.get("narrator_voice") or EDGE_DEFAULT_NARRATOR).strip()
        return {
            "version": 1,
            "refreshed": bool(raw.get("refreshed", False)),
            "refreshed_at": str(raw.get("refreshed_at") or ""),
            "voices": _sort_catalog_entries(voices),
            "custom_voices": custom,
            "narrator_voice": narrator or EDGE_DEFAULT_NARRATOR,
        }

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._state = state
        finally:
            Path(tmp).unlink(missing_ok=True)

    def all_entries(self) -> list[dict[str, Any]]:
        with self.lock:
            out: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in self._state["voices"]:
                low = item["short_name"].lower()
                if low in seen:
                    continue
                seen.add(low)
                out.append({**item, "custom": False})
            for short in self._state["custom_voices"]:
                if short.lower() in seen:
                    continue
                seen.add(short.lower())
                out.append(
                    {
                        "short_name": short,
                        "locale": "",
                        "gender": "",
                        "friendly_name": "",
                        "custom": True,
                    }
                )
            return out

    def voices(self) -> tuple[str, ...]:
        return tuple(item["short_name"] for item in self.all_entries())

    def custom_voices(self) -> tuple[str, ...]:
        with self.lock:
            return tuple(self._state["custom_voices"])

    def voice_genders(self) -> tuple[tuple[str, str], ...]:
        pairs = []
        for item in self.all_entries():
            gender = (item.get("gender") or "").strip().lower()
            if gender in ("female", "male"):
                pairs.append((item["short_name"], gender))
        return tuple(pairs)

    def meta(self, short_name: str) -> dict[str, Any] | None:
        key = (short_name or "").strip().lower()
        if not key:
            return None
        for item in self.all_entries():
            if item["short_name"].lower() == key:
                return item
        return None

    def narrator(self) -> str:
        with self.lock:
            active = self.voices()
            chosen = self._state.get("narrator_voice") or EDGE_DEFAULT_NARRATOR
            if chosen in active:
                return chosen
            return active[0] if active else EDGE_DEFAULT_NARRATOR

    def refreshed_at(self) -> str:
        with self.lock:
            return str(self._state.get("refreshed_at") or "")

    def set_narrator(self, voice: str) -> None:
        voice = (voice or "").strip()
        if not voice:
            raise ValueError("旁白音色不能为空")
        with self.lock:
            if voice not in self.voices():
                raise ValueError("所选旁白音色不在当前可用列表中")
            self._save({**self._state, "narrator_voice": voice})

    def add_custom_voice(self, short_name: str) -> str:
        short = (short_name or "").strip()
        if not _CUSTOM_VOICE_RE.match(short):
            raise ValueError("音色 ID 格式无效，请填写如 zh-CN-XiaoxiaoNeural 的标准 ShortName")
        with self.lock:
            if not any(v.lower() == short.lower() for v in self._state["custom_voices"]):
                self._save({**self._state, "custom_voices": [*self._state["custom_voices"], short]})
            return short

    def remove_custom_voice(self, short_name: str) -> None:
        key = (short_name or "").strip().lower()
        with self.lock:
            before = len(self._state["custom_voices"])
            remaining = [
                v for v in self._state["custom_voices"] if v.lower() != key
            ]
            if len(remaining) != before:
                self._save({**self._state, "custom_voices": remaining})

    def update_catalog(self, entries: list[dict[str, Any]]) -> int:
        cleaned: list[dict[str, str]] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            short = str(entry.get("short_name") or entry.get("ShortName") or "").strip()
            if not short or short.lower() in seen:
                continue
            seen.add(short.lower())
            cleaned.append(
                {
                    "short_name": short,
                    "locale": str(entry.get("locale") or entry.get("Locale") or "").strip(),
                    "gender": str(entry.get("gender") or entry.get("Gender") or "").strip(),
                    "friendly_name": str(
                        entry.get("friendly_name") or entry.get("FriendlyName") or ""
                    ).strip(),
                }
            )
        if not cleaned:
            raise ValueError("刷新返回了空的音色目录")
        with self.lock:
            self._save({
                **self._state,
                "voices": _sort_catalog_entries(cleaned),
                "refreshed": True,
                "refreshed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            return len(cleaned)


@dataclass(frozen=True)
class EdgeComponentSpec:
    root: Path
    python: Path
    libs_dir: Path | None
    manifest: dict[str, Any]


def locate_edge_component(component_dir: Path | None = None) -> EdgeComponentSpec:
    candidates: list[Path] = []
    if component_dir is not None:
        candidates.append(Path(component_dir))
    else:
        env_path = os.environ.get("NOVEL_TTS_EDGE_COMPONENT", "").strip()
        if env_path:
            candidates.append(Path(env_path))
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "edge_component")
        if exe_dir.name.lower() == "python":
            candidates.append(exe_dir.parent / "edge_component")
        # Source checkout root: src/novel_tts/providers/edge_tts.py -> parents[3]
        repo_root = Path(__file__).resolve().parents[3]
        candidates.append(repo_root / "edge_component")

    found: Path | None = None
    for candidate in candidates:
        if (candidate / "component.json").is_file():
            found = candidate.resolve()
            break
    if found is None:
        raise EdgeComponentError(
            "未找到 Edge 在线语音组件，请安装或修复 edge_component 目录。已缓存的句子仍可离线播放。"
        )
    try:
        manifest = json.loads((found / "component.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EdgeComponentError("Edge 在线语音组件清单损坏，请重新安装组件。") from exc

    if (
        not isinstance(manifest, dict)
        or manifest.get("name") != "novel-tts-edge"
        or manifest.get("protocol_version") != EDGE_PROTOCOL_VERSION
    ):
        raise EdgeComponentError(
            "Edge 在线语音组件版本不兼容，请更新组件后重试。已缓存的句子仍可离线播放。"
        )

    libs_dir = found / "libs"
    libs = libs_dir if libs_dir.is_dir() else None
    python_candidates = [
        found / ".venv/Scripts/python.exe",
        found / ".venv/bin/python",
        found / "python/python.exe",
        found / "python/bin/python3",
        found / "python/bin/python",
    ]
    python = next((p for p in python_candidates if p.is_file()), None)
    if python is None and not getattr(sys, "frozen", False) and libs is not None:
        python = Path(sys.executable)
    if python is None:
        raise EdgeComponentError(
            "未找到 Edge 在线语音组件运行环境，请修复 edge_component 环境。已缓存的句子仍可离线播放。"
        )
    return EdgeComponentSpec(found, python, libs, manifest)


class EdgeAuthorization:
    """Chapter-scoped, one-way revocable authorization for Edge synthesis."""

    def __init__(
        self,
        book: str = "",
        chapter: str = "",
        *,
        allowed: bool = False,
        on_revoke: Callable[[EdgeAuthorization], None] | None = None,
    ):
        self.book = book
        self.chapter = chapter
        self.allowed = bool(allowed and book and chapter)
        self.revoked = threading.Event()
        if not self.allowed:
            self.revoked.set()
        self._on_revoke = on_revoke
        self._lock = threading.Lock()
        self._notified = False

    def is_valid(self) -> bool:
        return self.allowed and not self.revoked.is_set()

    def revoke(self) -> None:
        self.revoked.set()
        callback = None
        with self._lock:
            if not self._notified and self._on_revoke is not None:
                self._notified = True
                callback = self._on_revoke
        if callback is not None:
            callback(self)


class _PendingResponse:
    def __init__(self):
        self.done = threading.Event()
        self.result: dict[str, Any] | None = None
        self.error: Exception | None = None


class _QueueItem:
    def __init__(
        self,
        priority: int,
        seq: int,
        auth: EdgeAuthorization,
        cancel,
    ):
        self.priority = priority
        self.seq = seq
        self.auth = auth
        self.cancel = cancel
        self.removed = False

    def __lt__(self, other: _QueueItem) -> bool:
        return (self.priority, self.seq) < (other.priority, other.seq)


class EdgeWorkerRuntime:
    """Lazily starts the Edge stdio worker and enforces a single-slot priority queue."""

    def __init__(
        self,
        *,
        component_dir: Path | None = None,
        command: list[str] | None = None,
        env_extra: dict[str, str] | None = None,
        handshake_timeout: float = 10.0,
        cancel_grace: float = 2.0,
    ):
        self.component_dir = component_dir
        self._command_override = list(command) if command else None
        self._env_extra = dict(env_extra or {})
        self._handshake_timeout = handshake_timeout
        self._cancel_grace = cancel_grace

        self._io_lock = threading.Lock()
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._pending: dict[str, _PendingResponse] = {}
        self._hello: dict[str, Any] | None = None
        self._closed = False
        self.start_count = 0

        self._queue_cond = threading.Condition()
        self._heap: list[_QueueItem] = []
        self._seq = 0
        self._active_synth: tuple[str, EdgeAuthorization] | None = None

    @property
    def is_running(self) -> bool:
        with self._proc_lock:
            return self._proc is not None and self._proc.poll() is None

    def _build_launch(self) -> tuple[list[str], dict[str, str], Path | None]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env.update(self._env_extra)
        if self._command_override:
            return list(self._command_override), env, None
        spec = locate_edge_component(self.component_dir)
        py_paths = [str(spec.root)]
        if spec.libs_dir is not None:
            py_paths.insert(0, str(spec.libs_dir))
        existing_pp = env.get("PYTHONPATH", "")
        if existing_pp:
            py_paths.append(existing_pp)
        env["PYTHONPATH"] = os.pathsep.join(py_paths)
        cmd = [str(spec.python), "-u", "-m", "novel_tts_edge"]
        return cmd, env, spec.root

    def _ensure_started(self) -> subprocess.Popen[str]:
        with self._proc_lock:
            if self._closed:
                raise EdgeComponentError("Edge 组件已关闭")
            if self._proc is not None and self._proc.poll() is None:
                return self._proc
            cmd, env, cwd = self._build_launch()
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=str(cwd) if cwd else None,
                    env=env,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise EdgeComponentError(
                    f"无法启动 Edge 语音组件（{type(exc).__name__}）"
                ) from exc

            hello_box: dict[str, Any] = {}
            hello_ready = threading.Event()

            def read_stdout() -> None:
                try:
                    assert proc.stdout is not None
                    first = proc.stdout.readline()
                    if first:
                        try:
                            msg = json.loads(first)
                        except ValueError:
                            msg = None
                        if (
                            isinstance(msg, dict)
                            and msg.get("type") == "hello"
                            and msg.get("protocol_version") == EDGE_PROTOCOL_VERSION
                        ):
                            hello_box["hello"] = msg
                            hello_ready.set()
                        else:
                            hello_box["error"] = EdgeComponentError(
                                "Edge 语音组件握手协议版本不兼容"
                            )
                            hello_ready.set()
                            return
                    else:
                        hello_box["error"] = EdgeComponentError(
                            "Edge 语音组件启动后立即退出"
                        )
                        hello_ready.set()
                        return

                    for line in proc.stdout:
                        text = line.strip()
                        if not text:
                            continue
                        try:
                            msg = json.loads(text)
                        except ValueError:
                            continue
                        if not isinstance(msg, dict):
                            continue
                        job_id = str(msg.get("job_id") or "")
                        if not job_id:
                            continue
                        with self._io_lock:
                            pending = self._pending.pop(job_id, None)
                        if pending is not None:
                            pending.result = msg
                            pending.done.set()
                finally:
                    if not hello_ready.is_set():
                        hello_box["error"] = EdgeComponentError("Edge 语音组件意外退出")
                        hello_ready.set()
                    with self._io_lock:
                        abandoned = list(self._pending.values())
                        self._pending.clear()
                    for pending in abandoned:
                        pending.error = ProviderError(
                            "Edge 语音组件意外退出", retryable=True, outcome="unknown"
                        )
                        pending.done.set()

            reader = threading.Thread(
                target=read_stdout, name="edge-worker-stdout", daemon=True
            )
            reader.start()
            if not hello_ready.wait(self._handshake_timeout):
                self._terminate_proc(proc)
                raise EdgeComponentError("Edge 语音组件握手超时")
            if "error" in hello_box:
                self._terminate_proc(proc)
                raise hello_box["error"]
            self._proc = proc
            self._reader = reader
            self._hello = hello_box["hello"]
            self.start_count += 1
            return proc

    @staticmethod
    def _terminate_proc(proc: subprocess.Popen[str]) -> None:
        try:
            if proc.stdin:
                try:
                    proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    proc.stdin.flush()
                except OSError:
                    pass
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if proc.stdout:
                try:
                    proc.stdout.close()
                except OSError:
                    pass

    def _restart_proc(self, proc: subprocess.Popen[str]) -> None:
        with self._proc_lock:
            if self._proc is proc:
                self._proc = None
        self._terminate_proc(proc)

    def _send_json(self, proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._io_lock:
            if proc.stdin is None or proc.poll() is not None:
                raise ProviderError("Edge 语音组件未运行", retryable=True)
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except OSError as exc:
                raise ProviderError(
                    f"无法写入 Edge 语音组件管道（{type(exc).__name__}）", retryable=True
                ) from exc

    def _send_cancel(self, proc: subprocess.Popen[str], job_id: str) -> None:
        try:
            self._send_json(proc, {"cmd": "cancel", "job_id": job_id})
        except ProviderError:
            pass

    def _submit(self, proc: subprocess.Popen[str], payload: dict[str, Any]) -> _PendingResponse:
        job_id = payload["job_id"]
        pending = _PendingResponse()
        with self._io_lock:
            self._pending[job_id] = pending
        try:
            self._send_json(proc, payload)
        except BaseException:
            with self._io_lock:
                self._pending.pop(job_id, None)
            self._restart_proc(proc)
            raise
        return pending

    def revoke_auth(self, auth: EdgeAuthorization) -> None:
        cancel_target: tuple[subprocess.Popen[str] | None, str | None] = (None, None)
        with self._queue_cond:
            for item in self._heap:
                if item.auth is auth:
                    item.removed = True
            self._heap = [item for item in self._heap if not item.removed]
            heapq.heapify(self._heap)
            if self._active_synth is not None and self._active_synth[1] is auth:
                with self._proc_lock:
                    cancel_target = (self._proc, self._active_synth[0])
            self._queue_cond.notify_all()
        proc, job_id = cancel_target
        if proc is not None and job_id:
            self._send_cancel(proc, job_id)

    def _acquire_synth_slot(
        self,
        job_id: str,
        auth: EdgeAuthorization,
        cancel,
        priority: int,
        deadline: float,
    ) -> None:
        with self._queue_cond:
            self._seq += 1
            item = _QueueItem(priority, self._seq, auth, cancel)
            heapq.heappush(self._heap, item)
            try:
                while True:
                    # Prune cancelled or revoked items at heap head.
                    while self._heap and (
                        self._heap[0].removed
                        or self._heap[0].cancel.is_set()
                        or not self._heap[0].auth.is_valid()
                    ):
                        heapq.heappop(self._heap)
                    if cancel.is_set():
                        raise Cancelled()
                    if not auth.is_valid():
                        raise EdgeAuthorizationRequired()
                    if time.monotonic() >= deadline:
                        raise ProviderError("Edge 合成排队等待超时", retryable=False)
                    if (
                        self._active_synth is None
                        and self._heap
                        and self._heap[0] is item
                    ):
                        heapq.heappop(self._heap)
                        self._active_synth = (job_id, auth)
                        return
                    remaining = max(0.0, deadline - time.monotonic())
                    self._queue_cond.wait(timeout=min(0.05, remaining))
            except BaseException:
                item.removed = True
                self._queue_cond.notify_all()
                raise

    def _release_synth_slot(self, job_id: str) -> None:
        with self._queue_cond:
            if self._active_synth is not None and self._active_synth[0] == job_id:
                self._active_synth = None
                self._queue_cond.notify_all()

    def synthesize_once(
        self,
        *,
        job_id: str,
        text: str,
        voice: str,
        rate: str,
        pitch: str,
        volume: str,
        destination: Path,
        cancel,
        auth: EdgeAuthorization,
        priority: int,
        connect_timeout: float,
        receive_timeout: float,
        deadline: float,
        max_attempts: int,
    ) -> dict[str, Any]:
        self._acquire_synth_slot(job_id, auth, cancel, priority, deadline)
        try:
            if cancel.is_set():
                raise Cancelled()
            if not auth.is_valid():
                raise EdgeAuthorizationRequired()
            if time.monotonic() >= deadline:
                raise ProviderError("Edge 合成任务已超时", retryable=False)

            proc = self._ensure_started()
            # Order dispatch and revoke_auth's cancellation on the same queue.
            with self._queue_cond:
                if cancel.is_set():
                    raise Cancelled()
                if not auth.is_valid():
                    raise EdgeAuthorizationRequired()
                if time.monotonic() >= deadline:
                    raise ProviderError("Edge 合成任务已超时", retryable=False)
                pending = self._submit(
                    proc,
                    {
                        "cmd": "synthesize",
                        "job_id": job_id,
                        "text": text,
                        "voice": voice,
                        "rate": rate,
                        "pitch": pitch,
                        "volume": volume,
                        "destination": str(destination),
                        "connect_timeout": connect_timeout,
                        "receive_timeout": receive_timeout,
                        "max_mp3_bytes": MAX_MP3_BYTES,
                        "max_pcm_bytes": MAX_PCM_BYTES,
                        "max_duration_seconds": MAX_DURATION_SECONDS,
                        "max_attempts": max_attempts,
                    },
                )

            while True:
                if pending.done.wait(timeout=0.05):
                    break
                if cancel.is_set() or not auth.is_valid() or time.monotonic() >= deadline:
                    reason = (
                        "cancel"
                        if cancel.is_set()
                        else ("revoked" if not auth.is_valid() else "timeout")
                    )
                    self._send_cancel(proc, job_id)
                    if not pending.done.wait(timeout=self._cancel_grace):
                        with self._io_lock:
                            self._pending.pop(job_id, None)
                        self._restart_proc(proc)
                    destination.unlink(missing_ok=True)
                    if reason == "cancel":
                        exc = Cancelled()
                        exc.outcome = "unknown"
                        raise exc
                    if reason == "revoked":
                        exc = EdgeAuthorizationRequired("本章 Edge 授权已撤销，在途任务已取消")
                        exc.outcome = "unknown"
                        raise exc
                    raise ProviderError(
                        "Edge 合成任务超时并已取消", retryable=False, outcome="unknown"
                    )

            if pending.error is not None:
                self._restart_proc(proc)
                raise pending.error
            assert pending.result is not None
            return pending.result
        finally:
            self._release_synth_slot(job_id)

    def list_voices(
        self,
        cancel,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT,
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        if cancel.is_set():
            raise Cancelled()
        proc = self._ensure_started()
        job_id = uuid.uuid4().hex
        deadline = time.monotonic() + timeout
        pending = self._submit(
            proc,
            {
                "cmd": "list_voices",
                "job_id": job_id,
                "connect_timeout": connect_timeout,
                "receive_timeout": receive_timeout,
            },
        )

        while True:
            if pending.done.wait(timeout=0.05):
                break
            if cancel.is_set() or time.monotonic() >= deadline:
                self._send_cancel(proc, job_id)
                if not pending.done.wait(timeout=self._cancel_grace):
                    with self._io_lock:
                        self._pending.pop(job_id, None)
                    self._restart_proc(proc)
                if cancel.is_set():
                    raise Cancelled()
                raise ProviderError("刷新 Edge 音色目录超时", retryable=True)

        if pending.error is not None:
            self._restart_proc(proc)
            raise pending.error
        result = pending.result or {}
        if not result.get("ok"):
            raise ProviderError(
                str(result.get("message") or "刷新 Edge 音色目录失败"),
                retryable=bool(result.get("retryable", False)),
            )
        return list(result.get("voices") or [])

    def close(self) -> None:
        with self._proc_lock:
            self._closed = True
            proc, self._proc = self._proc, None
        with self._queue_cond:
            for item in self._heap:
                item.removed = True
            self._heap.clear()
            self._queue_cond.notify_all()
        if proc is not None:
            self._terminate_proc(proc)


class EdgeTTS:
    """TTSProvider implementation for Microsoft Edge online Read Aloud."""

    can_stream = False

    def __init__(
        self,
        runtime: EdgeWorkerRuntime,
        catalog: EdgeVoiceCatalog,
        *,
        allowed: bool = False,
        book: str = "",
        chapter: str = "",
        record: Callable[[dict[str, Any]], None] | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT,
        job_timeout: float = DEFAULT_JOB_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ):
        self.runtime = runtime
        self.catalog = catalog
        self.auth = EdgeAuthorization(
            book,
            chapter,
            allowed=allowed,
            on_revoke=runtime.revoke_auth,
        )
        self.record = record
        self.connect_timeout = connect_timeout
        self.receive_timeout = receive_timeout
        self.job_timeout = job_timeout
        self.max_attempts = max(1, int(max_attempts))

    def is_authorized(self) -> bool:
        return self.auth.is_valid()

    def check_synthesis_scope(self, book: str, chapter: str) -> None:
        if not self.is_authorized() or (book, chapter) != (self.auth.book, self.auth.chapter):
            raise EdgeAuthorizationRequired()

    def revoke(self) -> None:
        self.auth.revoke()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider=EDGE_PROVIDER,
            model=EDGE_MODEL,
            protocol=EDGE_PROTOCOL,
            revision=EDGE_MODEL_REVISION,
            voices=self.catalog.voices(),
            instruct=False,
            formats=("wav-24k-mono-s16",),
            input_limit=EDGE_INPUT_LIMIT,
            limit_unit="codepoints",
            alignment=False,
            cancellation="soft_or_hard",
            narrator_voice=self.catalog.narrator(),
            voice_genders=self.catalog.voice_genders(),
        )

    def prepare(self, text: str, voice: str, style: str = "") -> SynthesisRequest:
        # Purely local. Accept any non-empty voice ID (including retired voices)
        # so previously cached audio can always be found and played offline.
        voice_id = (voice or "").strip()
        if not voice_id:
            raise ValueError("音色 ID 不能为空")
        return SynthesisRequest(
            text=text,
            provider=EDGE_PROVIDER,
            model=EDGE_MODEL,
            model_revision=EDGE_MODEL_REVISION,
            voice=voice_id,
            origin="cloud",
            style="",  # Edge v1 declares instruct=False; keep style empty.
            language="auto",
            parameters=(
                ("pitch", "+0Hz"),
                ("rate", "+0%"),
                ("volume", "+0%"),
            ),
        )

    def _emit_diagnostic(self, row: dict[str, Any]) -> None:
        if self.record is None:
            return
        try:
            self.record(row)
        except Exception:
            pass

    def synthesize(
        self,
        request: SynthesisRequest,
        destination: Path,
        cancel,
        *,
        priority: int | None = None,
    ) -> AudioArtifact:
        if cancel.is_set():
            raise Cancelled()
        if not self.auth.is_valid():
            raise EdgeAuthorizationRequired()
        if not request.text or not request.text.strip():
            raise ProviderError("朗读文本为空白", retryable=False)
        if len(request.text) > EDGE_INPUT_LIMIT:
            raise ProviderError("单段超过 Edge 语音长度上限", retryable=False)
        active_voices = {v.lower() for v in self.catalog.voices()}
        if request.voice.lower() not in active_voices:
            raise EdgeVoiceUnavailableError(request.voice)

        prio = current_edge_priority() if priority is None else priority
        logical_job = uuid.uuid4().hex
        deadline = time.monotonic() + self.job_timeout
        total_attempts = 0
        any_sent = False

        while True:
            if cancel.is_set():
                exc = Cancelled()
                if any_sent:
                    exc.outcome = "unknown"
                raise exc
            if not self.auth.is_valid():
                exc = EdgeAuthorizationRequired("本章 Edge 授权已撤销")
                if any_sent:
                    exc.outcome = "unknown"
                raise exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError(
                    "Edge 合成任务超时",
                    retryable=False,
                    outcome="unknown" if any_sent else "known",
                )

            attempt_id = uuid.uuid4().hex
            started = time.monotonic()
            # Clamp per-attempt connect/receive timeouts to remaining job deadline.
            c_timeout = min(self.connect_timeout, max(1.0, remaining))
            r_timeout = min(self.receive_timeout, max(1.0, remaining))
            try:
                result = self.runtime.synthesize_once(
                    job_id=attempt_id,
                    text=request.text,
                    voice=request.voice,
                    rate="+0%",
                    pitch="+0Hz",
                    volume="+0%",
                    destination=destination,
                    cancel=cancel,
                    auth=self.auth,
                    priority=prio,
                    connect_timeout=c_timeout,
                    receive_timeout=r_timeout,
                    deadline=deadline,
                    max_attempts=self.max_attempts - total_attempts,
                )
            except Cancelled as exc:
                self._emit_diagnostic(
                    {
                        "attempt": attempt_id,
                        "job": logical_job,
                        "logical_id": request.key,
                        "provider": EDGE_PROVIDER,
                        "model": EDGE_MODEL,
                        "voice": request.voice,
                        "state": "cancelled",
                        "error_code": "cancelled",
                        "outcome": getattr(exc, "outcome", "unknown"),
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                )
                raise
            except EdgeAuthorizationRequired as exc:
                self._emit_diagnostic(
                    {
                        "attempt": attempt_id,
                        "job": logical_job,
                        "logical_id": request.key,
                        "provider": EDGE_PROVIDER,
                        "model": EDGE_MODEL,
                        "voice": request.voice,
                        "state": "revoked",
                        "error_code": "revoked",
                        "outcome": getattr(exc, "outcome", "unknown"),
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                )
                raise
            except ProviderError as exc:
                self._emit_diagnostic(
                    {
                        "attempt": attempt_id,
                        "job": logical_job,
                        "logical_id": request.key,
                        "provider": EDGE_PROVIDER,
                        "model": EDGE_MODEL,
                        "voice": request.voice,
                        "state": "failed",
                        "error_code": type(exc).__name__,
                        "outcome": exc.outcome,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                )
                raise

            elapsed_ms = int(
                result.get("elapsed_ms", (time.monotonic() - started) * 1000)
            )
            used = max(1, int(result.get("attempts_used", 1)))
            total_attempts += used
            sent = bool(result.get("sent", True))
            if sent:
                any_sent = True

            if result.get("ok"):
                if cancel.is_set():
                    destination.unlink(missing_ok=True)
                    exc = Cancelled()
                    exc.outcome = "unknown"
                    raise exc
                if not self.auth.is_valid():
                    destination.unlink(missing_ok=True)
                    exc = EdgeAuthorizationRequired("本章 Edge 授权已撤销，已丢弃迟到结果")
                    exc.outcome = "unknown"
                    raise exc
                artifact = inspect_wav(destination, request)
                self._emit_diagnostic(
                    {
                        "attempt": attempt_id,
                        "job": logical_job,
                        "logical_id": request.key,
                        "provider": EDGE_PROVIDER,
                        "model": EDGE_MODEL,
                        "voice": request.voice,
                        "state": "succeeded",
                        "frames": artifact.frames,
                        "attempts_used": total_attempts,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                return artifact

            error_code = str(result.get("error_code") or "unknown_error")
            message = str(result.get("message") or "Edge 合成失败")
            retryable = bool(result.get("retryable", False))
            retry_after = result.get("retry_after")
            self._emit_diagnostic(
                {
                    "attempt": attempt_id,
                    "job": logical_job,
                    "logical_id": request.key,
                    "provider": EDGE_PROVIDER,
                    "model": EDGE_MODEL,
                    "voice": request.voice,
                    "state": "cancelled" if error_code == "cancelled" else "failed",
                    "error_code": error_code,
                    "http_status": result.get("http_status"),
                    "attempts_used": total_attempts,
                    "outcome": "unknown" if sent else "known",
                    "elapsed_ms": elapsed_ms,
                }
            )

            if error_code == "cancelled":
                if not self.auth.is_valid() and not cancel.is_set():
                    exc = EdgeAuthorizationRequired("本章 Edge 授权已撤销，在途任务已取消")
                    if any_sent:
                        exc.outcome = "unknown"
                    raise exc
                exc = Cancelled()
                if any_sent:
                    exc.outcome = "unknown"
                raise exc

            if cancel.is_set():
                exc = Cancelled()
                if any_sent:
                    exc.outcome = "unknown"
                raise exc

            if error_code == "forbidden_403":
                raise ProviderError(
                    message,
                    retryable=False,
                    outcome="unknown" if any_sent else "known",
                )

            if not retryable or total_attempts >= self.max_attempts:
                raise ProviderError(
                    message,
                    retryable=False,
                    outcome="unknown" if any_sent else "known",
                )

            if error_code == "rate_limited_429":
                wait_seconds = (
                    float(retry_after)
                    if retry_after is not None and float(retry_after) >= 0
                    else min(2.0 * total_attempts, 10.0)
                )
                if time.monotonic() + wait_seconds >= deadline:
                    raise ProviderError(
                        "微软在线语音服务限流（HTTP 429），等待时间超过任务期限",
                        retryable=False,
                        outcome="unknown" if any_sent else "known",
                    )
            else:
                wait_seconds = min(0.5 * total_attempts, 4.0)
                if time.monotonic() + wait_seconds >= deadline:
                    raise ProviderError(
                        message,
                        retryable=False,
                        outcome="unknown" if any_sent else "known",
                    )

            outcome = _wait_either(cancel, self.auth.revoked, wait_seconds)
            if outcome == "cancel":
                exc = Cancelled()
                if any_sent:
                    exc.outcome = "unknown"
                raise exc
            if outcome == "revoked":
                exc = EdgeAuthorizationRequired("等待重试期间本章 Edge 授权已撤销")
                if any_sent:
                    exc.outcome = "unknown"
                raise exc
