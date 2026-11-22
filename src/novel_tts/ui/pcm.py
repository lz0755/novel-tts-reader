"""Play incoming mono PCM on the GUI thread. Positions are in source audio time."""

from array import array
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from novel_tts.infrastructure.audio import RATE


def pcm_format():
    fmt = QAudioFormat()
    fmt.setSampleRate(RATE)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return fmt


class PcmPlayback(QObject):
    finished = Signal()
    failed = Signal(str)
    positionChanged = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), pcm_format(), self)
        self.sink.setBufferSize(RATE * 2 // 8)
        self.device = None
        self.queue = deque()
        self.output = memoryview(b"")
        self.received = self.written = 0
        self.speed = self.block_speed = 1.0
        # (sink frame, corresponding source frame, speed). Speed changes only
        # affect audio that has not already been sent to the hardware.
        self.timeline = deque([(0, 0.0, 1.0)])
        self.sonic = None
        self.complete = self.flushed = self.paused = False
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.pump)

    def setVolume(self, volume):
        self.sink.setVolume(volume)

    def setPlaybackRate(self, speed):
        self.speed = speed

    def append(self, pcm):
        self.queue.append(memoryview(pcm))
        self.received += len(pcm) // 2
        if not self.paused:
            self.play()

    def finish(self):
        self.complete = True
        if not self.paused:
            self.pump()

    def play(self):
        if self.device is None:
            self.device = self.sink.start()
            if self.device is None:
                self.failed.emit("无法打开流式音频输出设备")
                return
        elif self.paused:
            self.sink.resume()
        self.paused = False
        self.timer.start()
        self.pump()

    def pause(self):
        self.paused = True
        self.timer.stop()
        self.sink.suspend()

    def stop(self):
        self.timer.stop()
        self.sink.reset()
        self.queue.clear()

    def duration(self):
        return round(self.received * 1000 / RATE)

    def position(self):
        played = min(self.written // 2, self.sink.processedUSecs() * RATE // 1_000_000)
        while len(self.timeline) > 1 and self.timeline[1][0] <= played:
            self.timeline.popleft()
        frame, source, speed = self.timeline[0]
        return min(self.duration(), round((source + (played - frame) * speed) * 1000 / RATE))

    def next_block(self):
        self.block_speed = self.speed
        if self.queue:
            part = self.queue.popleft()
            limit = 2 * (RATE // 25)
            if len(part) > limit:
                self.queue.appendleft(part[limit:])
                part = part[:limit]
            if self.speed == 1 and self.sonic is None:
                return part
            if self.sonic is None:
                from libsonic import SonicStream

                self.sonic = SonicStream(RATE, 1)
            self.sonic.set_speed(self.speed)
            self.sonic.write([v / 32768 for v in part.cast("h")])
        elif self.complete and not self.flushed:
            self.flushed = True
            if self.sonic is None:
                return memoryview(b"")
            self.sonic.flush()
        else:
            return memoryview(b"")
        samples = self.sonic.read(self.sonic.available_frames())
        pcm = array("h", (max(-32768, min(32767, round(v * 32768))) for v in samples))
        return memoryview(pcm).cast("B")

    def pump(self):
        if self.paused or self.device is None:
            return
        if self.sink.error() not in (QAudio.Error.NoError, QAudio.Error.UnderrunError):
            self.failed.emit("流式音频输出设备发生错误")
            return
        while (space := self.sink.bytesFree()) >= 2:
            if not self.output:
                self.output = self.next_block()
                if not self.output:
                    if self.queue or (self.complete and not self.flushed):
                        continue
                    break
            count = self.device.write(self.output[:space - space % 2].tobytes())
            if count < 0:
                self.failed.emit("流式音频写入失败")
                return
            if not count:
                break
            frame, source, speed = self.timeline[-1]
            if speed != self.block_speed:
                now = self.written // 2
                self.timeline.append((now, source + (now - frame) * speed, self.block_speed))
            self.written += count
            self.output = self.output[count:]
        self.positionChanged.emit(self.position())
        if self.flushed and not self.output and self.sink.state() == QAudio.State.IdleState:
            self.timer.stop()
            self.finished.emit()
