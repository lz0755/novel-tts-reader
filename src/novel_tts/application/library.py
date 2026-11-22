import os
import tempfile
import uuid
import wave
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Lock, RLock

from novel_tts.application.stream import PendingAudio
from novel_tts.domain.models import Cancelled, Ticket
from novel_tts.infrastructure.audio import export_chapter, inspect_wav


class Library:
    def __init__(self, paths, repository):
        self.paths = paths
        self.db = repository
        self.locks = [Lock() for _ in range(61)]
        self.mutex = RLock()
        self.pins = Counter()
        self.streams = {}

    @contextmanager
    def lease(self, key):
        with self.mutex:
            self.pins[key] += 1
        try:
            yield
        finally:
            with self.mutex:
                self.pins[key] -= 1
                if not self.pins[key]:
                    del self.pins[key]

    def audio(self, view, line, provider, event, cache_only=False, on_pcm=None, prefetch=False):
        req = provider.prepare(line.spoken, line.voice, line.style)
        if not (on_pcm or (prefetch and getattr(provider, "can_stream", False))):
            return self._audio(view, req, provider, event, cache_only)
        # A prefetched sentence may already be generating when the previous
        # sentence ends. Replay its prefix, then follow the same worker live.
        identity = (view.book, view.revision, provider, req.key)
        with self.mutex:
            stream = self.streams.get(identity)
            joined = stream is not None and stream.join(event)
            if not joined:
                stream = self.streams[identity] = PendingAudio(event)
        if joined:
            return stream.follow(event, on_pcm)

        def publish(pcm):
            stream.publish(pcm)
            if on_pcm and not event.is_set():
                on_pcm(pcm)

        try:
            artifact = self._audio(view, req, provider, stream, cache_only, publish)
        except BaseException as exc:
            stream.finish(error=exc)
            raise
        else:
            stream.finish(artifact)
            if event.is_set():
                raise Cancelled()
            return artifact
        finally:
            with self.mutex:
                if self.streams.get(identity) is stream:
                    del self.streams[identity]

    def _audio(self, view, req, provider, event, cache_only, on_pcm=None):
        key = req.key
        final = self.paths.cache / (key + ".wav")
        with self.lease(key), self.locks[int(key[:8], 16) % len(self.locks)]:
            self.db.call("guard", view.book, view.revision)
            if event.is_set():
                raise Cancelled()
            info = self.db.call("audio_info", key)
            if info and final.is_file():
                try:
                    artifact = inspect_wav(final, req)
                    if (artifact.sha256, artifact.frames) == info:
                        return artifact
                except (ValueError, EOFError, OSError, wave.Error):
                    pass
            if cache_only:
                raise ValueError("本章尚有未生成或损坏的片段，请先预生成")
            ticket = Ticket(
                uuid.uuid4().hex, uuid.uuid4().hex, key, view.book, view.revision
            )
            self.db.call("begin", ticket)
            temp = None
            try:
                fd, name = tempfile.mkstemp(
                    prefix=key + "-", suffix=".partial", dir=self.paths.cache
                )
                os.close(fd)
                temp = Path(name)
                if on_pcm is None:
                    artifact = provider.synthesize(req, temp, event)
                else:
                    artifact = provider.synthesize(req, temp, event, on_pcm=on_pcm)
                if artifact.request != req or artifact.path != temp:
                    raise ValueError("返回音频与请求不匹配")
                artifact = inspect_wav(temp, req)
                if event.is_set():
                    raise Cancelled()
                self.db.call("guard", view.book, view.revision)
                os.replace(temp, final)
                self.db.call("finish_audio", ticket, artifact)
                quota = self.db.call("cache_over_quota")
                if quota is not None:
                    self.clean(quota)
                return replace(artifact, path=final)
            except Exception as exc:
                self.db.call(
                    "end_job",
                    ticket,
                    "unknown"
                    if getattr(exc, "outcome", "") == "unknown"
                    else ("cancelled" if isinstance(exc, Cancelled) else "failed"),
                )
                raise
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)

    def set_quota(self, limit_bytes):
        self.db.call("set_quota", limit_bytes)
        return self.clean(limit_bytes)

    def clean(self, limit_bytes):
        entries = self.db.call("entries")
        files = []
        for key, _digest in entries:
            path = self.paths.cache / (key + ".wav")
            try:
                files.append((key, path, path.stat().st_size))
            except FileNotFoundError:
                pass
        total = sum(size for _, _, size in files)
        removed = 0
        for key, path, size in files:
            if total <= limit_bytes:
                break
            with self.mutex:
                if key in self.pins:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    continue
                self.db.call("forget_audio", key)
                total -= size
                removed += 1
        return {"removed": removed, "remaining_bytes": total}

    def chapter(
        self, view, provider, event, destination=None, notify=lambda done, total: None
    ):
        count = 0
        total = 0
        for page in range(view.page_count):
            if event.is_set():
                raise Cancelled()
            self.db.call("guard", view.book, view.revision)
            total += len(
                self.db.call("view", view.chapter, page, provider.capabilities()).lines
            )
        notify(0, total)

        def segments(stack):
            nonlocal count
            for page in range(view.page_count):
                current = self.db.call(
                    "view", view.chapter, page, provider.capabilities()
                )
                self.db.call("guard", view.book, view.revision)
                for line in current.lines:
                    if event.is_set():
                        raise Cancelled()
                    req = provider.prepare(line.spoken, line.voice, line.style)
                    if destination:
                        stack.enter_context(self.lease(req.key))
                    audio = self.audio(
                        current, line, provider, event, cache_only=bool(destination)
                    )
                    count += 1
                    notify(count, total)
                    yield line, audio
            self.db.call("guard", view.book, view.revision)

        with ExitStack() as stack:
            if destination:
                return export_chapter(
                    destination.parent,
                    destination.name,
                    view.chapter,
                    segments(stack),
                    event,
                    dict(
                        book=view.book,
                        chapter_title=view.title,
                        content_revision=view.revision,
                        import_revision=self.db.call(
                            "request", view.chapter, 0
                        ).import_revision,
                    ),
                )
            for _ in segments(stack):
                pass
        return count
