import bisect

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QTextEdit

LABELS = {"mock_fixture":"Mock 固定语音", "mock_tone":"Mock 测试音", "cloud":"云端语音", "local":"本地语音"}


class Playback:
    def paint_line(self):
        if not self.view or not self.view.lines:
            self.text.setExtraSelections([])
            self.speaker.setText("本页没有可朗读文字")
            return
        line = self.view.lines[self.index]
        cursor = self.text.textCursor()
        cursor.setPosition(self.mapping.to_qt(line.start))
        cursor.setPosition(self.mapping.to_qt(line.end,True),QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor("#ffd77d"))
        selection.format.setForeground(QColor("#25221c"))
        self.text.setExtraSelections([selection])
        cursor.clearSelection()
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()
        self.speaker.setText(dict(self.view.characters).get(line.speaker,"待确认"))
        self.voice.clear()
        self.voice.addItems(self.provider.capabilities().voices)
        self.voice.setCurrentText(line.voice)
        req = self.provider.prepare(line.spoken,line.voice,line.style)
        self.origin.setText(LABELS[req.origin]+(" · 临时替代音色" if line.substitute else ""))
        self.spoken.setText("朗读文本\n"+line.spoken)

    def clicked_text(self,pos):
        if self.view and self.view.lines:
            cp = self.mapping.from_qt(pos)
            self.seek(max(0,bisect.bisect_right([line.start for line in self.view.lines],cp)-1))

    def seek(self,index):
        if not self.view or not self.view.lines:
            return
        if not 0<=index<len(self.view.lines):
            page = self.view.page + (-1 if index<0 else 1)
            if 0<=page<self.view.page_count:
                self.load_page(page,cp=10**15 if index<0 else None,play=self.intent)
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
        epoch, view, line, provider = self.epoch, self.view, self.view.lines[self.index], self.provider
        def failed(exc):
            self.stop()
            self.note.setText(f"播放失败 · {exc}")
        event = self.run("准备音频",lambda e:self.library.audio(view,line,provider,e),
                         self.play_artifact,guard=lambda:self.epoch==epoch and self.intent,failed=failed,priority=2)
        if event:
            self.waiters.append(event)

    def play_artifact(self,artifact):
        if not QMediaDevices.audioOutputs():
            self.stop()
            self.note.setText("没有可用音频输出设备。可以预生成并导出 WAV。")
            return
        self.media_pin = self.library.lease(artifact.request.key)
        self.media_pin.__enter__()
        self.media = media = QMediaPlayer(self)
        self.audio = QAudioOutput(media)
        self.audio.setVolume(self.volume.value()/100)
        media.setAudioOutput(self.audio)
        self.current_key = artifact.request.key
        media.setPlaybackRate(float(self.speed.currentText()))
        self.pending_ms = self.resume[3] if self.resume and self.resume[2]==self.current_key else 0
        self.resume = None
        def status(state):
            if self.media is not media:
                return
            if state == QMediaPlayer.MediaStatus.LoadedMedia and self.intent:
                if self.pending_ms:
                    media.setPosition(min(self.pending_ms,max(0,media.duration()-1)))
                    self.pending_ms = 0
                media.play()
            elif state == QMediaPlayer.MediaStatus.EndOfMedia and self.intent:
                if self.index+1<len(self.view.lines) or self.view.page+1<self.view.page_count:
                    self.seek(self.index+1)
                else:
                    self.stop()
                    self.note.setText("本章播放完毕")
        def error(_code,message):
            if self.media is media:
                self.stop()
                self.note.setText("播放器错误 · "+message)
        def changed(state):
            if self.media is media:
                playing = state==QMediaPlayer.PlaybackState.PlayingState
                self.note.setText("正在播放 · "+LABELS[artifact.request.origin] if playing else "播放已暂停")
                self.play_button.setText("暂停" if playing else "继续")
        media.mediaStatusChanged.connect(status)
        media.errorOccurred.connect(error)
        media.playbackStateChanged.connect(changed)
        media.positionChanged.connect(lambda ms: self.media is media and self.pos.setText(f"{ms/1000:.1f} / {media.duration()/1000:.1f} 秒"))
        media.setSource(QUrl.fromLocalFile(str(artifact.path)))
        self.prefetch()

    def prefetch(self):
        if not self.intent or not self.view:
            return
        view,provider = self.view,self.provider
        for line in view.lines[self.index+1:self.index+3]:
            event = self.run("预取",lambda e,l=line:self.library.audio(view,l,provider,e),lambda _:None,
                             guard=lambda:False,priority=-1)
            if event:
                self.waiters.append(event)

    def speed_changed(self,value):
        if self.media:
            self.media.setPlaybackRate(float(value))
        self.save_position()

    def save_position(self):
        if self.view and self.view.lines:
            line = self.view.lines[self.index]
            if self.media:
                key, ms = self.current_key,self.media.position()
            elif self.resume and self.resume[0]==self.view.chapter and self.resume[1]==line.start:
                key, ms = self.resume[2],self.resume[3]
            else:
                key, ms = "",0
            self.repo.submit("save_progress",self.view.book,self.view.chapter,line.start,key,ms,float(self.speed.currentText()))

    def stop(self):
        self.save_position()
        if self.media and self.view and self.view.lines:
            self.resume = (self.view.chapter,self.view.lines[self.index].start,self.current_key,self.media.position(),float(self.speed.currentText()))
        self.epoch += 1
        self.intent = False
        for event in self.waiters:
            event.set()
        self.waiters.clear()
        media, self.media = self.media, None
        self.audio = None
        if media:
            media.stop()
            media.setSource(QUrl())
            media.deleteLater()
        if self.media_pin:
            self.media_pin.__exit__(None,None,None)
            self.media_pin = None
        self.play_button.setText("播放")
        self.pos.setText("0.0 / 0.0 秒")
