"""Exercise the installed reader in a throwaway library without contacting a provider."""

import json
import time

from PySide6.QtCore import QTimer


def start_check(app, window, root, screenshot=None):
    output = root / "章节输出"
    timer = QTimer(window)
    timer.setInterval(30)
    deadline = time.monotonic() + 60
    stage = 0

    def finish(code):
        timer.stop()
        window.close()
        app.exit(code)

    def tick():
        nonlocal stage
        if time.monotonic() > deadline:
            timer.stop()
            print(json.dumps({"error": window.note.text(), "stage": stage}), flush=True)
            finish(3)
        elif stage == 0:
            stage = 1
            window.demo()
        elif stage == 1 and window.view:
            if len(window.chapters) != 2:
                return finish(4)
            window.seek(2)
            if screenshot:
                window.grab().save(str(screenshot))
            stage = 2
            window.generate()
        elif stage == 2 and "预生成本章完成" in window.note.text():
            stage = 3
            window.chapter_task(output)
        elif stage == 3 and (output / "manifest.json").exists():
            manifest = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )
            segments = manifest["segments"]
            if (
                not manifest["complete"]
                or len(segments) != len(window.view.lines)
                or not (output / "chapter.wav").is_file()
            ):
                return finish(5)
            print(
                json.dumps(
                    dict(
                        check="mock_desktop",
                        chapters=len(window.chapters),
                        segments=len(segments),
                        frames=manifest["frames"],
                        origin=sorted({s["origin"] for s in segments}),
                    )
                ),
                flush=True,
            )
            finish(0)

    timer.timeout.connect(tick)
    timer.start()
