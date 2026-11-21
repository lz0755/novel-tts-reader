import errno
from dataclasses import replace
from threading import Event
from unittest.mock import patch

import pytest

from novel_tts.application.buffering import prefetch_count, wall_seconds
from novel_tts.domain.models import Stale, Ticket
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.store import Store
from novel_tts.providers.mock import MockTTS
from novel_tts.ui.window import ReaderWindow
from test_mock_flow import wait


def test_reconcile_and_quota(book):
    store,library,tts,view = book
    art = library.audio(view,view.lines[0],tts,Event())
    with library.lease(art.request.key):
        assert library.clean(0)["removed"]==0 and art.path.exists()
    assert library.clean(0)["removed"]==1
    art = library.audio(view,view.lines[0],tts,Event())
    art.path.unlink()
    (store.paths.cache/("f"*64+".wav")).write_bytes(b"orphan")
    (store.paths.cache/"record.partial").write_bytes(b"partial")
    report = store.recover()
    assert report["partial"]==report["missing"]==report["orphan"]==1
    assert store.audio_info(art.request.key) is None
    with patch("novel_tts.application.library.os.replace",side_effect=OSError(errno.ENOSPC,"disk full")):
        with pytest.raises(OSError):
            library.audio(view,view.lines[0],tts,Event())
    assert not list(store.paths.cache.iterdir()) and not store.entries()


def test_old_attempt_and_deletion(book):
    store,library,tts,view = book
    art = library.audio(view,view.lines[0],tts,Event())
    first = Ticket("job","first",art.request.key,view.book,view.revision)
    store.begin(first)
    store.begin(replace(first,attempt_id="second"))
    with pytest.raises(Stale):
        store.finish_audio(first,art)
    store.delete_book(view.book)
    with pytest.raises(Stale):
        store.finish_audio(replace(first,attempt_id="second"),art)
    assert not store.library()


def test_twenty_jumps(app,tmp_path):
    win = ReaderWindow(Paths(tmp_path))
    try:
        win.demo()
        wait(app,lambda:bool(win.view))
        win.provider = MockTTS("late_result_after_cancel")
        arrivals = []
        win.play_artifact = lambda artifact:arrivals.append(artifact.request.text)
        win.start()
        for i in range(20):
            win.seek(i % len(win.view.lines))
        wait(app,lambda:bool(arrivals))
        assert arrivals==[win.view.lines[win.index].spoken]
    finally:
        win.close()


def test_different_rates():
    assert [wall_seconds(20,r) for r in (1,1.5,2)]==[20,20/1.5,10]
    assert prefetch_count(8,1,[3,3,3,3])==1
    assert prefetch_count(8,2,[3,3,3,3])==4
