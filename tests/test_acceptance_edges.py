import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from novel_tts.domain.annotations import MockLLM, RuleNarration
from novel_tts.domain.models import AnnotationRequest, Parent
from novel_tts.domain.normalize import spoken_form
from novel_tts.domain.text import decoded_offsets, read_draft
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.store import Store
from novel_tts.infrastructure.threads import Repository
from novel_tts.application.library import Library


def test_corpus_and_pronunciation():
    folder = Path(__file__).parents[1] / "fixtures"
    corpus = json.loads((folder / "regression.json").read_text(encoding="utf-8"))
    parts = [p for ch in corpus["chapters"] for p in ch["parents"]]
    actual = [g for p in parts for g in p["gold"]]
    assert len(corpus["chapters"]) == 10 and len(corpus["characters"]) == 20
    assert len(actual) == 350
    assert sum(g["speaker"] not in ("unknown", "narrator") for g in actual) == 150
    assert sum(g["speaker"] == "unknown" for g in actual) == 30
    assert sum(p["kind"] == "multi" for p in parts) == 30
    for p in parts:
        at = 0
        for g in p["gold"]:
            assert at == g["start"] < g["end"] <= len(p["text"])
            at = g["end"]
        assert at == len(p["text"])
    assert (
        len((folder / "pronunciation.tsv").read_text(encoding="utf-8").splitlines())
        == 51
    )


def test_normalization_and_graphemes():
    assert (
        spoken_form("2026年，12下，3.5米，型号B12。")
        == "二零二六年，十二下，三点五米，型号B12。"
    )
    draft = read_draft("甲\r\n乙\r丙".encode(), "格式")
    assert decoded_offsets(draft, range(len(draft.source) + 1)) == {
        0: 0,
        1: 1,
        2: 3,
        3: 4,
        4: 5,
        5: 6,
    }
    req = AnnotationRequest(
        "b", "ch", "r", 0, "job", (Parent("p0", 0, "甲。\u0301“乙”\u0301。"),)
    )
    for llm in (MockLLM(), RuleNarration()):
        llm.analyze_chunk(req)


def test_corrupt_cache_self_heals(book):
    store, lib, tts, view = book
    art = lib.audio(view, view.lines[0], tts, Event())
    art.path.write_bytes(b"bad audio")
    fixed = lib.audio(view, view.lines[0], tts, Event())
    assert fixed.sha256 == art.sha256 and fixed.frames == art.frames
    assert lib.set_quota(0)["remaining_bytes"] == 0


def test_disk_full_during_staging(book, monkeypatch):
    import errno

    store, lib, tts, view = book

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "模拟磁盘写满")

    monkeypatch.setattr("novel_tts.application.library.tempfile.mkstemp", full)
    with pytest.raises(OSError):
        lib.audio(view, view.lines[0], tts, Event())
    assert store.db.execute("SELECT state FROM jobs").fetchone()[0] == "failed"
    assert not store.entries()


def test_concurrent_audio_deduplication(book, app, monkeypatch):
    store, _, tts, view = book
    # Use the same dedicated QThread arrangement as the desktop.
    repo = Repository(lambda: Store(store.paths))
    lib = Library(store.paths, repo)
    original = tts.synthesize
    calls = []

    def recorded(*args):
        calls.append(threading.get_ident())
        return original(*args)

    monkeypatch.setattr(tts, "synthesize", recorded)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: lib.audio(view, view.lines[0], tts, Event()), range(8)
                )
            )
        assert len(calls) == 1 and len({r.sha256 for r in results}) == 1
    finally:
        repo.close()


def test_migration_backup_and_preexisting_data(tmp_path):
    from novel_tts.infrastructure.store import SCHEMA

    paths = Paths(tmp_path)
    old = paths.data / "library.sqlite3"
    with sqlite3.connect(old) as conn:
        conn.executescript(SCHEMA + "PRAGMA user_version=1;")
        conn.execute("INSERT INTO books VALUES('id','原书','sha','utf8',X'31',1,0)")
    db = Store(paths)
    try:
        assert db.library() == [("id", "原书")]
        assert db.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with sqlite3.connect(old.with_suffix(".before-v2.sqlite3")) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
            assert backup.execute("SELECT title FROM books").fetchone()[0] == "原书"
        assert db.db.execute("SELECT import_revision FROM books").fetchone()[0]
    finally:
        db.close()
