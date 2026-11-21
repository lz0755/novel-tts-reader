import bisect
import tempfile
import wave
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from novel_tts.domain.models import AudioArtifact, Cancelled
from novel_tts.infrastructure.audio import RATE


@dataclass(frozen=True)
class Member:
    index: int
    first_frame: int
    audio: AudioArtifact


@dataclass
class AudioBatch:
    temporary: tempfile.TemporaryDirectory
    path: Path
    members: tuple[Member, ...]
    frames: int

    @property
    def request(self):
        return self.members[0].audio.request

    @property
    def seconds(self):
        return self.frames / RATE

    def member_at(self, ms):
        frame = int(ms * RATE / 1000)
        i = bisect.bisect_right([m.first_frame for m in self.members], frame) - 1
        return self.members[max(0, i)]


def playable(library, view, index, provider, event, combine):
    line = view.lines[index]
    first = library.audio(view, line, provider, event)
    if not combine or line.speaker in ("narrator", "unknown") or len(line.spoken) > 20:
        return first
    members = [Member(index, 0, first)]
    frames = first.frames
    previous = line
    with ExitStack() as pins:
        pins.enter_context(library.lease(first.request.key))
        for i in range(index + 1, min(len(view.lines), index + 4)):
            part = view.lines[i]
            gap = view.source[previous.end - view.offset : part.start - view.offset]
            if (
                (part.speaker, part.voice, part.style)
                != (line.speaker, line.voice, line.style)
                or len(part.spoken) > 20
                or gap.strip()
            ):
                break
            req = provider.prepare(part.spoken, part.voice, part.style)
            if req.origin != first.request.origin:
                break
            pins.enter_context(library.lease(req.key))
            try:
                audio = library.audio(view, part, provider, event, cache_only=True)
            except ValueError:
                break
            members.append(Member(i, frames, audio))
            frames += audio.frames
            previous = part
        if len(members) == 1:
            return first
        directory = tempfile.TemporaryDirectory(
            prefix="play-batch-", dir=library.paths.cache, ignore_cleanup_errors=True
        )
        path = Path(directory.name) / "batch.wav"
        try:
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                for member in members:
                    with wave.open(str(member.audio.path), "rb") as source:
                        while data := source.readframes(8192):
                            if event.is_set():
                                raise Cancelled()
                            output.writeframesraw(data)
            return AudioBatch(directory, path, tuple(members), frames)
        except BaseException:
            directory.cleanup()
            raise
