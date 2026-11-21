import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event

import httpx
import pytest

from novel_tts.domain.models import Cancelled
from novel_tts.domain.text import read_draft
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.profiles import Profiles, DEFAULTS
from novel_tts.infrastructure.store import Store
from novel_tts.providers.llm import RemoteLLM
from test_llm import answer


@pytest.mark.parametrize(
    "point,code,counter", [("partial", 71, "partial"), ("after_rename", 72, "orphan")]
)
def test_actual_crash_at_publication(tmp_path, point, code, counter):
    root = Path(__file__).parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    p = subprocess.run(
        [sys.executable, str(root / "tests/crash_worker.py"), str(tmp_path), point],
        env=env,
        timeout=20,
    )
    assert p.returncode == code
    store = Store(Paths(tmp_path))
    try:
        assert store.recovery[counter] == 1 and not store.entries()
        book, title = store.library()[0]
        assert title == "雨声" and store.progress(book)
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.db.execute("SELECT state FROM jobs").fetchone()[0] == "interrupted"
    finally:
        store.close()


def test_cancelled_import_rolls_back(book):
    store, _, _, _ = book
    before = store.library()
    draft = read_draft("第一章 灯火\n一盏灯。".encode(), "失败导入")
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        store.add_book(draft, draft.boundaries, cancel)
    assert store.library() == before


def test_keyring_session_fallback_and_redaction(tmp_path, monkeypatch):
    import keyring

    profile = DEFAULTS["openai"]
    profiles = Profiles(tmp_path)

    def failed(*_args):
        raise keyring.errors.NoKeyringError("missing")

    monkeypatch.setattr(keyring, "set_password", failed)
    monkeypatch.setattr(keyring, "get_password", failed)
    fake = "fixture-only-secret-for-this-test"
    assert "仅本次会话" in profiles.save(profile, fake, True)
    assert profiles.credential(profile) == fake and fake not in profiles.path.read_text(
        encoding="utf-8"
    )
    monkeypatch.setenv("OPENAI_API_KEY", fake)
    with pytest.raises(ValueError, match="未配置"):
        profiles.credential(replace(profile, endpoint="https://other.example/v1"))


def test_explicit_capability_downgrade_with_two_attempt_limit(book):
    store, _, _, view = book
    req = store.request(view.chapter, 0)
    calls = []
    log = []

    def server(r):
        obj = json.loads(r.content)
        calls.append(obj)
        if len(calls) == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "unsupported_value",
                        "param": "response_format.type",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                answer(json.loads(obj["messages"][-1]["content"]))
                            )
                        },
                    }
                ]
            },
        )

    remote = RemoteLLM(
        replace(DEFAULTS["custom"], output="json_schema"),
        "",
        transport=httpx.MockTransport(server),
        record=log.append,
    )
    assert remote.analyze_chunk(req, Event()).status == "completed"
    assert [x["response_format"]["type"] for x in calls] == [
        "json_schema",
        "json_object",
    ]
    assert len({r["job"] for r in log}) == 1
    assert [r["number"] for r in log if r["state"] == "sent"] == [1, 2]


def test_temporary_voice_mapping_does_not_overwrite_manual(book):
    store, _, tts, view = book
    caps = tts.capabilities()
    cid = view.lines[0].speaker
    store.set_voice(view, cid, caps, "星禾")
    assert store.binding(view.book, cid, replace(caps, voices=("旁白",))) == (
        "旁白",
        True,
    )
    assert store.binding(
        view.book, cid, replace(caps, model="other", voices=("voiceX",))
    ) == ("voiceX", False)
    assert store.binding(view.book, cid, caps) == ("星禾", False)


def test_failed_migration_rolls_back_and_releases_db(tmp_path):
    import sqlite3
    from novel_tts.infrastructure.store import SCHEMA

    paths = Paths(tmp_path)
    database = paths.data / "library.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            SCHEMA + "CREATE TABLE aliases(unexpected TEXT); PRAGMA user_version=1;"
        )
    with pytest.raises(sqlite3.OperationalError):
        Store(paths)
    with sqlite3.connect(database, timeout=0.2) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        db.execute("DROP TABLE aliases")
    assert database.with_suffix(".before-v2.sqlite3").is_file()
    repaired = Store(paths)
    try:
        assert repaired.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        repaired.close()
