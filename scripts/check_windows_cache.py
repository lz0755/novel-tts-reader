"""Run with the Windows desktop Python to verify external Windows sharing semantics."""

import ctypes
import json
import os
import platform
import tempfile
from ctypes import wintypes
from pathlib import Path
from threading import Event

from novel_tts.application.library import Library
from novel_tts.domain.text import read_draft
from novel_tts.infrastructure.paths import Paths, RESOURCES
from novel_tts.infrastructure.store import Store
from novel_tts.providers.mock import MockTTS


class Repository:
    """This single-thread probe calls the repository in its owning thread."""

    def __init__(self, store):
        self.store = store

    def call(self, method, *args):
        return getattr(self.store, method)(*args)


def main():
    if os.name != "nt":
        raise SystemExit("此探测需要 Windows 运行时")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    with tempfile.TemporaryDirectory() as temp:
        paths = Paths(Path(temp))
        store = Store(paths)
        try:
            tts = MockTTS()
            draft = read_draft((RESOURCES / "demo.txt").read_bytes(), "灯渡")
            book = store.add_book(draft, draft.boundaries, Event())
            chapter = store.toc(book)[0][0]
            view = store.view(chapter, 0, tts.capabilities())
            library = Library(paths, Repository(store))
            art = library.audio(view, view.lines[0], tts, Event())
            # GENERIC_READ and FILE_SHARE_READ, without FILE_SHARE_DELETE.
            handle = api.CreateFileW(str(art.path), 0x80000000, 1, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                held = library.clean(0)
                assert art.path.exists() and len(store.entries()) == 1
            finally:
                if not api.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            released = library.clean(0)
            assert not art.path.exists() and not store.entries()
            print(
                json.dumps(
                    dict(platform=platform.platform(), held=held, released=released)
                )
            )
        finally:
            store.close()


if __name__ == "__main__":
    main()
