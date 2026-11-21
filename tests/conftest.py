import os
from threading import Event

import pytest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from novel_tts.application.library import Library
from novel_tts.domain.text import read_draft
from novel_tts.infrastructure.paths import Paths, RESOURCES
from novel_tts.infrastructure.store import Store
from novel_tts.providers.mock import MockTTS


class InlineRepository:
    def __init__(self, store):
        self.store = store

    def call(self, name, *args):
        return getattr(self.store,name)(*args)


@pytest.fixture
def book(tmp_path):
    paths = Paths(tmp_path/"中文 书库")
    store = Store(paths)
    provider = MockTTS()
    draft = read_draft((RESOURCES/"demo.txt").read_bytes(),"灯渡")
    book_id = store.add_book(draft,draft.boundaries,Event())
    chapters = store.toc(book_id)
    view = store.view(chapters[0][0],0,provider.capabilities())
    try:
        yield store,Library(paths,InlineRepository(store)),provider,view
    finally:
        store.close()


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
