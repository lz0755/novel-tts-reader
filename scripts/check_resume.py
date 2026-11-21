"""Verify progress with the actual Qt media backend on a system with an audio output."""

import json
import tempfile
import time
from pathlib import Path

from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication

from novel_tts.infrastructure.paths import Paths
from novel_tts.ui.window import ReaderWindow


def main():
    app = QApplication([])
    if not QMediaDevices.audioOutputs():
        raise SystemExit("无音频输出设备")

    def wait(predicate):
        until = time.monotonic() + 20
        while not predicate():
            if time.monotonic() > until:
                raise TimeoutError("播放器未达到预期状态")
            app.processEvents()
            time.sleep(0.003)

    with tempfile.TemporaryDirectory() as folder:
        paths = Paths(Path(folder))
        win = ReaderWindow(paths)
        try:
            win.demo()
            wait(lambda: bool(win.view))
            win.seek(1)
            win.start()
            wait(lambda: win.media and win.media.position() >= 350)
            win.toggle()
            old_ms = win.media.position()
            old_key = win.current_key
            cp = win.view.lines[win.index].start
        finally:
            win.close()
        reopened = ReaderWindow(paths)
        try:
            wait(lambda: bool(reopened.view))
            assert (
                reopened.view.lines[reopened.index].start == cp
                and reopened.resume[2] == old_key
            )
            reopened.start()
            wait(lambda: reopened.media and reopened.media.position() > 0)
            same = reopened.media.position()
            reopened.toggle()
            assert abs(same - old_ms) <= 100
            reopened.stop()
            old_view = reopened.view
            cid = old_view.lines[reopened.index].speaker
            reopened.repo.call(
                "set_voice", old_view, cid, reopened.provider.capabilities(), "星禾"
            )
        finally:
            reopened.close()
        changed = ReaderWindow(paths)
        try:
            wait(lambda: bool(changed.view))
            assert changed.view.lines[changed.index].start == cp
            changed.start()
            wait(lambda: changed.media is not None)
            reset = changed.pending_ms
            assert changed.current_key != old_key and reset == 0
        finally:
            changed.close()
    print(
        json.dumps(
            dict(
                original_cp=cp,
                saved_ms=old_ms,
                same_audio_ms=same,
                new_audio_initial_ms=reset,
                different_fingerprint=True,
            )
        )
    )


if __name__ == "__main__":
    main()
