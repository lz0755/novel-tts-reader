import json
import time
import wave
from threading import Event, get_ident

import pytest
from PySide6.QtTest import QTest

from novel_tts.infrastructure.paths import Paths
from novel_tts.ui.window import ReaderWindow


def wait(app, predicate):
    deadline = time.monotonic() + 8
    while not predicate():
        app.processEvents()
        QTest.qWait(5)
        assert time.monotonic() < deadline


def test_chapter_cache_export(book, tmp_path):
    store, library, tts, view = book
    event = Event()
    output = tmp_path / "完整 导出"
    with pytest.raises(ValueError, match="尚有未生成"):
        library.chapter(view, tts, event, output)
    assert not output.exists()
    assert library.chapter(view, tts, event) == len(view.lines)
    assert library.chapter(view, tts, event, output) == output
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    segments = manifest["segments"]
    assert [s["start_cp"] for s in segments] == [line.start for line in view.lines]
    assert {s["origin"] for s in segments} == {"mock_fixture"}
    assert len({s["voice"] for s in segments}) == 3
    with wave.open(str(output / "chapter.wav")) as wav:
        assert wav.getnframes() == sum(s["frames"] for s in segments)
    with pytest.raises(FileExistsError):
        library.chapter(view, tts, event, output)
    line = view.lines[0]
    store.set_voice(view, line.speaker, tts.capabilities(), "星禾")
    view = store.view(view.chapter, 0, tts.capabilities())
    assert library.audio(view, view.lines[0], tts, event).request.origin == "mock_tone"


def test_window_import_generate_and_resume(app, tmp_path):
    paths = Paths(tmp_path)
    win = ReaderWindow(paths)
    try:
        win.show()
        win.demo()
        wait(app, lambda: bool(win.view))
        assert len(win.chapters) == 2
        assert win.repo.worker.thread_id != get_ident()
        win.seek(2)
        cp = win.view.lines[2].start
        win.generate()
        wait(app, lambda: "预生成本章完成" in win.note.text())
        win.chapter_task(tmp_path / "test export")
        wait(app, lambda: (tmp_path / "test export/manifest.json").exists())
        selections = win.text.extraSelections()
        assert selections[0].cursor.selectedText() == win.view.lines[2].display
    finally:
        win.close()
    reopened = ReaderWindow(paths)
    try:
        wait(app, lambda: bool(reopened.view))
        assert reopened.view.lines[reopened.index].start == cp
    finally:
        reopened.close()
