"""Play via a real Qt backend. Record device names and media positions."""
import json
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaDevices,QMediaPlayer
from PySide6.QtWidgets import QApplication

from novel_tts.infrastructure.paths import Paths
from novel_tts.ui.window import ReaderWindow


def main():
    app=QApplication([])
    scratch=tempfile.TemporaryDirectory()
    win=ReaderWindow(Paths(Path(scratch.name)))
    win.show()
    devices=[d.description() for d in QMediaDevices.audioOutputs()]
    if not devices:
        print(json.dumps({"result":"blocked","devices":[]}))
        win.close()
        return 2
    win.demo()
    trace=[]
    begin=time.monotonic()
    stage=0
    def tick():
        nonlocal stage
        if time.monotonic()-begin>25:
            timer.stop()
            print(json.dumps({"result":"failed","devices":devices,"status":win.note.text(),"trace":trace},ensure_ascii=False),flush=True)
            win.close()
            app.exit(3)
        elif stage==0 and win.view:
            win.start()
            stage=1
        elif stage==1 and win.media and win.media.position()>150:
            trace.append(["playing",win.index,win.media.position()])
            win.toggle()
            stage=2
        elif stage==2 and win.media and win.media.playbackState()==QMediaPlayer.PlaybackState.PausedState:
            trace.append(["paused",win.index,win.media.position()])
            win.toggle()
            win.speed.setCurrentText("1.5")
            win.seek(2)
            stage=3
        elif stage==3 and win.media and win.index==2 and win.media.position()>150:
            trace.append(["jumped",win.index,win.media.position(),win.media.playbackRate()])
            timer.stop()
            print(json.dumps({"result":"passed","devices":devices,"trace":trace},ensure_ascii=False),flush=True)
            win.close()
            app.exit(0)
    timer=QTimer()
    timer.timeout.connect(tick)
    timer.start(20)
    result=app.exec()
    scratch.cleanup()
    return result


if __name__=="__main__":
    raise SystemExit(main())
