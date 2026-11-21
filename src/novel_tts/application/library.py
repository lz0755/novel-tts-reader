import hashlib
import os
import tempfile
import uuid
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Lock, RLock

from novel_tts.domain.models import AudioArtifact, Cancelled, Ticket
from novel_tts.infrastructure.audio import RATE, export_chapter, inspect_wav


class Library:
    def __init__(self, paths, repository):
        self.paths = paths
        self.db = repository
        self.locks = [Lock() for _ in range(61)]
        self.mutex = RLock()
        self.pins = Counter()

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

    def audio(self, view, line, provider, event, cache_only=False):
        req = provider.prepare(line.spoken,line.voice,line.style)
        key = req.key
        final = self.paths.cache / (key + ".wav")
        with self.lease(key), self.locks[int(key[:8],16) % len(self.locks)]:
            self.db.call("guard",view.book,view.revision)
            if event.is_set():
                raise Cancelled()
            info = self.db.call("audio_info",key)
            if info and final.is_file():
                try:
                    artifact = inspect_wav(final,req)
                    if (artifact.sha256,artifact.frames) == info:
                        return artifact
                except (ValueError, EOFError, OSError):
                    pass
            if cache_only:
                raise ValueError("本章尚有未生成或损坏的片段，请先预生成")
            ticket = Ticket(uuid.uuid4().hex,uuid.uuid4().hex,key,view.book,view.revision)
            self.db.call("begin",ticket)
            fd, temp = tempfile.mkstemp(prefix=key+"-",suffix=".partial",dir=self.paths.cache)
            os.close(fd)
            temp = Path(temp)
            try:
                artifact = provider.synthesize(req,temp,event)
                if artifact.request != req or artifact.path != temp:
                    raise ValueError("返回音频与请求不匹配")
                artifact = inspect_wav(temp,req)
                if event.is_set():
                    raise Cancelled()
                self.db.call("guard",view.book,view.revision)
                os.replace(temp,final)
                self.db.call("finish_audio",ticket,artifact)
                return replace(artifact,path=final)
            except Exception as exc:
                self.db.call("end_job",ticket,getattr(exc,"outcome","failed"))
                raise
            finally:
                temp.unlink(missing_ok=True)

    def chapter(self, view, provider, event, destination=None, notify=lambda done,total:None):
        count = 0
        def segments(stack):
            nonlocal count
            for page in range(view.page_count):
                current = self.db.call("view",view.chapter,page,provider.capabilities())
                self.db.call("guard",view.book,view.revision)
                for line in current.lines:
                    if event.is_set():
                        raise Cancelled()
                    req = provider.prepare(line.spoken,line.voice,line.style)
                    if destination:
                        stack.enter_context(self.lease(req.key))
                    audio = self.audio(current,line,provider,event,cache_only=bool(destination))
                    count += 1
                    notify(count,view.page_count)
                    yield line,audio
            self.db.call("guard",view.book,view.revision)
        with ExitStack() as stack:
            if destination:
                return export_chapter(destination.parent,destination.name,view.chapter,segments(stack),event)
            for _ in segments(stack):
                pass
        return count
