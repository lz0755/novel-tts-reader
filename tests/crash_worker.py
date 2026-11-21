"""Subprocess exclusively used by the file/SQLite crash-point regression."""

import os
import sys
from pathlib import Path
from threading import Event

from PySide6.QtCore import QCoreApplication
from novel_tts.application.library import Library
from novel_tts.domain.text import read_draft
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.store import Store
from novel_tts.infrastructure.threads import Repository
from novel_tts.providers.mock import MockTTS


def main():
    paths = Paths(Path(sys.argv[1]))
    point = sys.argv[2]

    class CrashStore(Store):
        def finish_audio(self, *args):
            if point == "after_rename":
                os._exit(72)
            return super().finish_audio(*args)

    app = QCoreApplication([])
    repo = Repository(lambda: CrashStore(paths))
    draft = read_draft("第一章 雨声\n窗外有雨。".encode(), "雨声")
    book = repo.call("add_book", draft, draft.boundaries, Event())
    tts = MockTTS()
    if point == "partial":

        def fail(_req, dest, _cancel):
            dest.write_bytes(b"RIFF")
            os._exit(71)

        tts.synthesize = fail
    chapter = repo.call("toc", book)[0][0]
    view = repo.call("view", chapter, 0, tts.capabilities())
    repo.call("save_progress", book, chapter, view.lines[0].start, "", 0, 1)
    Library(paths, repo).audio(view, view.lines[0], tts, Event())
    repo.close()
    app.quit()


if __name__ == "__main__":
    main()
