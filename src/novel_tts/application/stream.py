from threading import Condition

from novel_tts.domain.models import Cancelled


class PendingAudio:
    """Share an in-progress sentence when playback catches up with prefetch."""

    def __init__(self, event):
        self.condition = Condition()
        self.events = {event}
        self.chunks = []
        self.done = False
        self.artifact = self.error = None

    def join(self, event):
        with self.condition:
            if self.done or self.is_set():
                return False
            self.events.add(event)
            return True

    def is_set(self):
        with self.condition:
            return all(event.is_set() for event in self.events)

    def wait(self, timeout):
        with self.condition:
            return self.condition.wait_for(self.is_set, timeout)

    def publish(self, pcm):
        with self.condition:
            self.chunks.append(pcm)
            self.condition.notify_all()

    def finish(self, artifact=None, error=None):
        with self.condition:
            self.artifact, self.error, self.done = artifact, error, True
            self.condition.notify_all()

    def follow(self, event, on_pcm):
        offset = 0
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(
                        lambda: self.done or event.is_set() or len(self.chunks) > offset, 0.1
                    )
                    if event.is_set():
                        raise Cancelled()
                    if self.error:
                        raise self.error
                    packets = self.chunks[offset:] if on_pcm else ()
                    offset = len(self.chunks)
                    done = self.done
                for packet in packets:
                    if event.is_set():
                        raise Cancelled()
                    on_pcm(packet)
                if done:
                    return self.artifact
        finally:
            with self.condition:
                self.events.discard(event)
                self.condition.notify_all()
