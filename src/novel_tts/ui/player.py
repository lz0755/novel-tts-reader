import bisect
from novel_tts.application.batch import AudioBatch, playable
from novel_tts.infrastructure.audio import RATE

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QTextEdit

LABELS = {
    "mock_fixture": "Mock 固定语音",
    "mock_tone": "Mock 测试音",
    "cloud": "云端语音",
    "local": "本地语音",
}


class Playback:
    def paint_line(self):
        if not self.view or not self.view.lines:
            self.text.setExtraSelections([])
            self.speaker.setText("本页没有可朗读文字")
            return
        line = self.view.lines[self.index]
        cursor = self.text.textCursor()
        cursor.setPosition(self.mapping.to_qt(line.start))
        cursor.setPosition(
            self.mapping.to_qt(line.end, True), QTextCursor.MoveMode.KeepAnchor
        )
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor("#ffd77d"))
        selection.format.setForeground(QColor("#25221c"))
        self.text.setExtraSelections([selection])
        cursor.clearSelection()
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()
        self.speaker.setText(dict(self.view.characters).get(line.speaker, "待确认"))
        self.voice.clear()
        self.voice.addItems(self.provider.capabilities().voices)
        self.voice.setCurrentText(line.voice)
        req = self.provider.prepare(line.spoken, line.voice, line.style)
        self.origin.setText(
            LABELS[req.origin] + (" · 临时替代音色" if line.substitute else "")
        )
        self.spoken.setText(
            "朗读文本\n"
            + line.spoken
            + (
                (
                    "\n表演提示 · "
                    + line.style
                    + ("（已应用）" if req.style else "（此音色未应用）")
                )
                if line.style
                else ""
            )
        )

    def clicked_text(self, pos):
        if self.view and self.view.lines:
            cp = self.mapping.from_qt(pos)
            self.seek(
                max(
                    0,
                    bisect.bisect_right([line.start for line in self.view.lines], cp)
                    - 1,
                )
            )

    def seek(self, index):
        if not self.view or not self.view.lines:
            return
        if not 0 <= index < len(self.view.lines):
            page = self.view.page + (-1 if index < 0 else 1)
            if 0 <= page < self.view.page_count:
                self.load_page(page, cp=10**15 if index < 0 else None, play=self.intent)
            return
        was_playing = self.intent
        self.stop()
        self.resume = None
        self.index = index
        self.paint_line()
        self.save_position()
        if was_playing:
            self.start()

    def toggle(self):
        if self.intent:
            self.intent = False
            for event in self.waiters:
                event.set()
            self.waiters.clear()
            self.prefetching.clear()
            if self.media:
                self.media.pause()
            else:
                self.epoch += 1
            self.play_button.setText("继续")
        elif self.media:
            self.intent = True
            self.media.play()
        else:
            self.start()

    def start(self):
        if not self.view or not self.view.lines:
            return
        self.intent = True
        self.play_button.setText("暂停")
        self.note.setText("等待音频")
        epoch, view, provider = (
            self.epoch,
            self.view,
            self.provider,
        )

        def failed(exc):
            self.stop()
            self.note.setText(f"播放失败 · {exc}")

        index, combine = self.index, self.batch_action.isChecked()
        event = self.run(
            "准备音频",
            lambda e: playable(self.library, view, index, provider, e, combine),
            self.play_artifact,
            guard=lambda: self.epoch == epoch and self.intent,
            failed=failed,
            priority=2,
        )
        if event:
            self.waiters.append(event)

    def play_artifact(self, artifact):
        if not QMediaDevices.audioOutputs():
            self.stop()
            self.note.setText("没有可用音频输出设备。可以预生成并导出 WAV。")
            return
        self.batch = artifact if isinstance(artifact, AudioBatch) else None
        self.member_start_ms = 0
        self.prefetching.discard(artifact.request.key)
        self.durations[artifact.request.key] = (
            self.batch.members[0].audio.seconds if self.batch else artifact.seconds
        )
        self.media_pin = self.library.lease(artifact.request.key)
        self.media_pin.__enter__()
        self.media = media = QMediaPlayer(self)
        self.audio = QAudioOutput(media)
        self.audio.setVolume(self.volume.value() / 100)
        media.setAudioOutput(self.audio)
        self.current_key = artifact.request.key
        media.setPlaybackRate(float(self.speed.currentText()))
        self.pending_ms = (
            self.resume[3] if self.resume and self.resume[2] == self.current_key else 0
        )
        self.resume = None

        def status(state):
            if self.media is not media:
                return
            if state == QMediaPlayer.MediaStatus.LoadedMedia and self.intent:
                if self.pending_ms:
                    media.setPosition(
                        min(self.pending_ms, max(0, media.duration() - 1))
                    )
                    self.pending_ms = 0
                media.play()
            elif state == QMediaPlayer.MediaStatus.EndOfMedia and self.intent:
                if self.batch:
                    self.index = self.batch.members[-1].index
                if (
                    self.index + 1 < len(self.view.lines)
                    or self.view.page + 1 < self.view.page_count
                ):
                    self.advance()
                else:
                    self.stop()
                    self.note.setText("本章播放完毕")

        def error(_code, message):
            if self.media is media:
                self.stop()
                self.note.setText("播放器错误 · " + message)

        def changed(state):
            if self.media is media:
                playing = state == QMediaPlayer.PlaybackState.PlayingState
                self.note.setText(
                    "正在播放 · " + LABELS[artifact.request.origin]
                    if playing
                    else "播放已暂停"
                )
                self.play_button.setText("暂停" if playing else "继续")

        media.mediaStatusChanged.connect(status)
        media.errorOccurred.connect(error)
        media.playbackStateChanged.connect(changed)

        def position(ms):
            if self.media is not media:
                return
            if self.batch:
                member = self.batch.member_at(ms)
                self.current_key = member.audio.request.key
                self.member_start_ms = round(member.first_frame * 1000 / RATE)
                if self.index != member.index:
                    self.index = member.index
                    self.paint_line()
                duration = member.audio.seconds
            else:
                duration = media.duration() / 1000
            self.pos.setText(
                f"{max(0, ms - self.member_start_ms) / 1000:.1f} / {duration:.1f} 秒"
            )

        media.positionChanged.connect(position)
        media.setSource(QUrl.fromLocalFile(str(artifact.path)))
        self.prefetch()

    def advance(self):
        view = self.view
        if self.index + 1 >= len(view.lines):
            self.load_page(view.page + 1, play=True)
            return
        self.save_position()
        self.release_media()
        self.resume = None
        self.index += 1
        self.paint_line()
        self.start()

    def prefetch(self):
        if not self.intent or not self.view:
            return
        from novel_tts.application.buffering import prefetch_count

        view, provider, epoch = self.view, self.provider, self.epoch
        remaining = (
            max(0, (self.media.duration() - self.media.position()) / 1000)
            if self.media
            else 0.0
        )
        last = self.batch.members[-1].index if self.batch else self.index
        lines = view.lines[last + 1 : last + 5]
        keys = [
            provider.prepare(line.spoken, line.voice, line.style).key for line in lines
        ]
        seconds = [
            self.durations.get(key, max(0.5, len(line.spoken) / 4))
            for key, line in zip(keys, lines)
        ]
        count = prefetch_count(remaining, float(self.speed.currentText()), seconds)
        self.waiters = [
            event for event in self.waiters if event in self.bg.events.values()
        ]
        for line, key in zip(lines[:count], keys):
            if key in self.prefetching:
                continue
            self.prefetching.add(key)

            def ready(artifact):
                if len(self.durations) > 512:
                    self.durations.pop(next(iter(self.durations)))
                self.durations[artifact.request.key] = artifact.seconds

            def failed(exc, k=key):
                self.prefetching.discard(k)

            event = self.run(
                "预取",
                lambda e, l=line: self.library.audio(view, l, provider, e),
                ready,
                failed=failed,
                guard=lambda: self.epoch == epoch,
                priority=-1,
            )
            if event:
                self.waiters.append(event)
            else:
                self.prefetching.discard(key)

    def speed_changed(self, value):
        if self.media:
            self.media.setPlaybackRate(float(value))
            self.prefetch()
        self.save_position()

    def save_position(self):
        if self.view and self.view.lines:
            line = self.view.lines[self.index]
            if self.media:
                key, ms = (
                    self.current_key,
                    max(0, self.media.position() - self.member_start_ms),
                )
            elif (
                self.resume
                and self.resume[0] == self.view.chapter
                and self.resume[1] == line.start
            ):
                key, ms = self.resume[2], self.resume[3]
            else:
                key, ms = "", 0
            self.repo.submit(
                "save_progress",
                self.view.book,
                self.view.chapter,
                line.start,
                key,
                ms,
                float(self.speed.currentText()),
            )

    def stop(self):
        self.save_position()
        if self.media and self.view and self.view.lines:
            self.resume = (
                self.view.chapter,
                self.view.lines[self.index].start,
                self.current_key,
                max(0, self.media.position() - self.member_start_ms),
                float(self.speed.currentText()),
            )
        self.epoch += 1
        self.intent = False
        for event in self.waiters:
            event.set()
        self.waiters.clear()
        self.prefetching.clear()
        self.release_media()
        self.play_button.setText("播放")
        self.pos.setText("0.0 / 0.0 秒")

    def release_media(self):
        media, self.media = self.media, None
        self.audio = None
        if media:
            media.stop()
            media.setSource(QUrl())
            media.deleteLater()
        if self.batch:
            self.batch.temporary.cleanup()
            self.batch = None
        if self.media_pin:
            self.media_pin.__exit__(None, None, None)
            self.media_pin = None
        self.play_button.setText("播放")
        self.pos.setText("0.0 / 0.0 秒")
