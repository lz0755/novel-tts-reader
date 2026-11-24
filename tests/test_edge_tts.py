import hashlib
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from novel_tts.application.library import Library
from novel_tts.domain.models import Cancelled, ProviderError
from novel_tts.domain.text import read_draft
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.store import Store
from novel_tts.infrastructure.threads import Repository
from novel_tts.providers.edge_tts import (
    EDGE_PROVIDER,
    EdgeAuthorizationRequired,
    EdgeComponentError,
    EdgeTTS,
    EdgeVoiceCatalog,
    EdgeVoiceUnavailableError,
    EdgeWorkerRuntime,
    PRIORITY_PLAYBACK,
    PRIORITY_PREFETCH,
    edge_priority,
    locate_edge_component,
)
from novel_tts.providers.local import LocalTTS, MODELS
from novel_tts.providers.mock import MockTTS
from novel_tts.ui.window import ReaderWindow
from test_mock_flow import wait

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_WORKER = Path(__file__).resolve().parent / "edge_mock_worker.py"


def _edge_python() -> Path:
    spec = locate_edge_component(REPO_ROOT / "edge_component")
    return spec.python


def _make_runtime(tmp_path: Path, config: dict, **kwargs):
    cfg_path = tmp_path / "edge_cfg.json"
    log_path = tmp_path / "edge_log.jsonl"
    cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    env_extra = {
        "PYTHONPATH": os.pathsep.join(
            [str(REPO_ROOT / "edge_component"), str(Path(__file__).resolve().parent)]
        )
    }
    runtime = EdgeWorkerRuntime(
        command=[str(_edge_python()), "-u", str(MOCK_WORKER), str(cfg_path), str(log_path)],
        env_extra=env_extra,
        **kwargs,
    )
    return runtime, cfg_path, log_path


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _set_cfg(cfg_path: Path, config: dict) -> None:
    cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def test_edge_audio_integrity_and_error_cases(book, tmp_path):
    store, library, _, view = book
    runtime, cfg_path, _ = _make_runtime(tmp_path, {"mode": "tone_fixture"})
    catalog = EdgeVoiceCatalog(store.paths.data)
    provider = EdgeTTS(
        runtime, catalog, allowed=True, book=view.book, chapter=view.chapter, max_attempts=2
    )
    try:
        edge_view = store.view(view.chapter, 0, provider.capabilities())
        # A fixed 440 Hz tone, encoded as ten MPEG-2 Layer III frames.
        art = library.audio(edge_view, edge_view.lines[0], provider, Event())
        assert (art.rate, art.channels, art.width, art.frames) == (24000, 1, 2, 5760)
        assert art.request.provider == EDGE_PROVIDER and art.request.origin == "cloud"
        with wave.open(str(art.path), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (
                24000,
                1,
                2,
                5760,
            )
            assert any(wav.readframes(5760))

        # 2. Empty, truncated, corrupt, size_limit, duration_limit produce errors,
        # zero new cache entries, and zero leftover .partial files.
        before_entries = set(store.entries())
        for idx, bad_mode in enumerate(
            ("empty", "truncated", "corrupt", "size_limit", "duration_limit"), start=1
        ):
            _set_cfg(cfg_path, {"mode": bad_mode})
            line = edge_view.lines[idx % len(edge_view.lines)]
            # Ensure we pick an uncached line by altering voice or picking distinct line.
            if line.spoken == edge_view.lines[0].spoken and line.voice == edge_view.lines[0].voice:
                line = edge_view.lines[2]
            with pytest.raises(ProviderError):
                library.audio(edge_view, line, provider, Event())
            assert set(store.entries()) == before_entries
            assert not list(store.paths.cache.glob("*.partial"))
    finally:
        runtime.close()


def test_edge_missing_and_incompatible_component_allows_offline_cache(book, tmp_path):
    store, library, _, view = book
    runtime, _, _ = _make_runtime(tmp_path, {"mode": "ok"})
    catalog = EdgeVoiceCatalog(store.paths.data)
    good = EdgeTTS(runtime, catalog, allowed=True, book=view.book, chapter=view.chapter)
    try:
        edge_view = store.view(view.chapter, 0, good.capabilities())
        cached_art = library.audio(edge_view, edge_view.lines[0], good, Event())
    finally:
        runtime.close()

    # Missing component directory
    missing_rt = EdgeWorkerRuntime(component_dir=tmp_path / "does-not-exist")
    offline_provider = EdgeTTS(
        missing_rt, catalog, allowed=True, book=view.book, chapter=view.chapter
    )
    try:
        # Cached sentence plays offline without starting worker!
        again = library.audio(edge_view, edge_view.lines[0], offline_provider, Event())
        assert again.sha256 == cached_art.sha256
        assert missing_rt.start_count == 0
        # Uncached sentence reports clear missing component error.
        with pytest.raises(EdgeComponentError, match="未找到 Edge"):
            library.audio(edge_view, edge_view.lines[1], offline_provider, Event())
    finally:
        missing_rt.close()

    # Incompatible component protocol version
    bad_comp = tmp_path / "bad_comp"
    bad_comp.mkdir()
    (bad_comp / "component.json").write_text(
        json.dumps({"name": "novel-tts-edge", "protocol_version": 99}), encoding="utf-8"
    )
    bad_rt = EdgeWorkerRuntime(component_dir=bad_comp)
    bad_provider = EdgeTTS(
        bad_rt, catalog, allowed=True, book=view.book, chapter=view.chapter
    )
    try:
        assert library.audio(edge_view, edge_view.lines[0], bad_provider, Event()).sha256 == cached_art.sha256
        with pytest.raises(EdgeComponentError, match="版本不兼容"):
            library.audio(edge_view, edge_view.lines[1], bad_provider, Event())
    finally:
        bad_rt.close()


def test_edge_expired_deadline_after_worker_start_sends_no_text(book, tmp_path, monkeypatch):
    store, library, _, view = book
    runtime, _, log = _make_runtime(tmp_path, {"mode": "ok"})
    provider = EdgeTTS(runtime, EdgeVoiceCatalog(store.paths.data), allowed=True,
                       book=view.book, chapter=view.chapter, job_timeout=5)
    clock = SimpleNamespace(monotonic=lambda: 100)
    monkeypatch.setattr("novel_tts.providers.edge_tts.time", clock)
    start_worker = runtime._ensure_started

    def slow_handshake():
        proc = start_worker()
        clock.monotonic = lambda: 106
        return proc

    monkeypatch.setattr(runtime, "_ensure_started", slow_handshake)
    try:
        page = store.view(view.chapter, 0, provider.capabilities())
        with pytest.raises(ProviderError, match="超时"):
            library.audio(page, page.lines[0], provider, Event())
        assert runtime.start_count == 1 and _read_log(log) == []
        assert not store.entries() and not list(store.paths.cache.glob("*.partial"))
    finally:
        runtime.close()


def test_edge_authorization_chapter_switch_and_zero_leak(app, tmp_path):
    paths = Paths(tmp_path / "书库 授权")
    gate = tmp_path / "gate.txt"
    runtime, cfg_path, log_path = _make_runtime(
        tmp_path, {"mode": "ok", "gate_file": str(gate)}
    )
    win = ReaderWindow(paths, force_mock=True)
    win.edge_runtime.close()
    win.edge_runtime = runtime
    try:
        win.demo()
        wait(app, lambda: bool(win.view) and len(win.chapters) == 2)
        win.edge_voice(False)
        wait(app, lambda: win.view and win.provider.capabilities().provider == EDGE_PROVIDER)

        # Cache-only mode, browsing/refreshing catalog, and failed export send zero text.
        assert win.edge_runtime.start_count == 0
        win.chapter_task(tmp_path / "missing-export")
        wait(app, lambda: "尚有未生成" in win.note.text())
        assert _read_log(log_path) == []

        # Catalog refresh sends metadata request only, never novel text, and preserves auth state.
        voices = runtime.list_voices(Event())
        win.edge_catalog.update_catalog(voices)
        saved_catalog = win.edge_catalog.path.read_bytes()
        _set_cfg(cfg_path, {"list_fail": True})
        with pytest.raises(ProviderError):
            runtime.list_voices(Event())
        assert win.edge_catalog.path.read_bytes() == saved_catalog
        assert EdgeVoiceCatalog(paths.data).voices() == win.edge_catalog.voices()
        _set_cfg(cfg_path, {"mode": "ok", "gate_file": str(gate)})
        assert not win.provider.is_authorized()
        assert [e for e in _read_log(log_path) if e["kind"] == "synthesize"] == []

        # Authorize Chapter A and start background prefetch tasks holding provider_a.
        view_a = win.view
        win._authorize_edge_chapter(view_a)
        provider_a = win.provider
        assert provider_a.is_authorized()

        with ThreadPoolExecutor(2) as pool:
            try:
                active = pool.submit(
                    win.library.audio, view_a, view_a.lines[0], provider_a, Event(), prefetch=True
                )
                wait(app, lambda: any(e["kind"] == "synthesize" for e in _read_log(log_path)))
                line = view_a.lines[1]
                queued = pool.submit(
                    provider_a.synthesize, provider_a.prepare(line.spoken, line.voice),
                    tmp_path / "queued.partial", Event(), priority=PRIORITY_PREFETCH,
                )
                wait(app, lambda: bool(runtime._heap))
                win.toc.setCurrentRow(1)
                wait(app, lambda: win.view and win.view.chapter == win.chapters[1][0])
                assert not provider_a.is_authorized() and not win.provider.is_authorized()
                view_b = win.view
                win._authorize_edge_chapter(view_b)
                for task in (active, queued):
                    with pytest.raises(EdgeAuthorizationRequired):
                        task.result(timeout=4)
            finally:
                gate.write_text("open", encoding="utf-8")
                provider_a.revoke()

        assert win.provider is not provider_a and win.provider.is_authorized()

        # Any lingering Chapter A task trying to use provider_a STILL cannot borrow B's auth!
        with pytest.raises(EdgeAuthorizationRequired):
            win.library.audio(view_a, view_a.lines[1], provider_a, Event())

        # Chapter B synthesizes cleanly.
        art_b = win.library.audio(view_b, view_b.lines[0], win.provider, Event())
        assert art_b.frames == 5760
        sent = [e["text"] for e in _read_log(log_path) if e["kind"] == "synthesize"]
        assert sent == [view_a.lines[0].spoken, view_b.lines[0].spoken]
    finally:
        win.close()

    # Restart restores Edge source in cache-only mode; zero text sent on startup/open!
    before_restart_sends = len([e for e in _read_log(log_path) if e["kind"] == "synthesize"])
    reopened = ReaderWindow(paths)
    try:
        wait(app, lambda: bool(reopened.view))
        assert isinstance(reopened.provider, EdgeTTS)
        assert not reopened.provider.is_authorized()
        assert reopened.provider.runtime.start_count == 0
        assert len([e for e in _read_log(log_path) if e["kind"] == "synthesize"]) == before_restart_sends
    finally:
        reopened.close()


def test_edge_stop_cancel_403_429_and_retry_bounds(book, tmp_path):
    store, library, _, view = book
    runtime, cfg_path, log_path = _make_runtime(tmp_path, {"mode": "stall"}, cancel_grace=1.0)
    catalog = EdgeVoiceCatalog(store.paths.data)
    records = []
    provider = EdgeTTS(
        runtime,
        catalog,
        allowed=True,
        book=view.book,
        chapter=view.chapter,
        record=records.append,
        job_timeout=4.0,
        max_attempts=3,
    )
    try:
        edge_view = store.view(view.chapter, 0, provider.capabilities())

        # 1. Stall (no first packet) cancelled promptly.
        cancel = Event()
        timer = threading.Timer(0.25, cancel.set)
        timer.start()
        t0 = time.monotonic()
        with pytest.raises(Cancelled):
            library.audio(edge_view, edge_view.lines[0], provider, cancel)
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0
        assert not store.entries()
        assert not list(store.paths.cache.glob("*.partial"))

        # 2. HTTP 403 ends immediately without extra retries.
        _set_cfg(cfg_path, {"mode": "forbidden_403"})
        before = len(_read_log(log_path))
        with pytest.raises(ProviderError, match="403|拒绝访问"):
            library.audio(edge_view, edge_view.lines[0], provider, Event())
        assert len(_read_log(log_path)) - before == 1

        # 3. HTTP 429 with Retry-After exceeding task deadline ends immediately.
        _set_cfg(cfg_path, {"mode": "rate_limit_429", "retry_after": 30.0})
        t0 = time.monotonic()
        with pytest.raises(ProviderError, match="429|限流"):
            library.audio(edge_view, edge_view.lines[0], provider, Event())
        assert time.monotonic() - t0 < 2.0

        # 4. Cancel while waiting for 429 Retry-After exits promptly without a 2nd send.
        _set_cfg(cfg_path, {"mode": "rate_limit_429", "retry_after": 2.0})
        before = len(_read_log(log_path))
        cancel2 = Event()
        threading.Timer(0.35, cancel2.set).start()
        t0 = time.monotonic()
        with pytest.raises(Cancelled):
            library.audio(edge_view, edge_view.lines[0], provider, cancel2)
        assert time.monotonic() - t0 < 1.5
        assert len(_read_log(log_path)) - before == 1

        # 5. Transient drops have bounded retries (max_attempts=3).
        _set_cfg(cfg_path, {"mode": "drop", "fail_count": 10})
        before = len(_read_log(log_path))
        with pytest.raises(ProviderError):
            library.audio(edge_view, edge_view.lines[0], provider, Event())
        assert len(_read_log(log_path)) - before == 3
        assert not store.entries()
        # Diagnostics never contain novel text!
        for row in records:
            assert edge_view.lines[0].spoken not in json.dumps(row, ensure_ascii=False)
    finally:
        runtime.close()


@pytest.mark.parametrize("budget", [2, 3])
def test_edge_retry_after_counts_worker_and_upstream_attempts(book, tmp_path, budget):
    store, library, _, view = book
    runtime, _, log = _make_runtime(tmp_path, {"mode": "throttle_then_internal_retry"})
    records = []
    provider = EdgeTTS(
        runtime, EdgeVoiceCatalog(store.paths.data), allowed=True,
        book=view.book, chapter=view.chapter, max_attempts=budget, record=records.append,
    )
    try:
        page = store.view(view.chapter, 0, provider.capabilities())
        if budget == 2:
            with pytest.raises(ProviderError, match="403"):
                library.audio(page, page.lines[0], provider, Event())
            assert not store.entries() and not list(store.paths.cache.glob("*.partial"))
        else:
            assert library.audio(page, page.lines[0], provider, Event()).frames == 5760
        sends = _read_log(log)
        assert len(sends) == records[-1]["attempts_used"] == budget
        assert sends[1]["stamp"] - sends[0]["stamp"] >= 0.09
    finally:
        runtime.close()


def test_edge_priority_playback_beats_queued_prefetch(book, tmp_path):
    store, _, _, view = book
    gate = tmp_path / "prio_gate.txt"
    runtime, cfg_path, log_path = _make_runtime(
        tmp_path, {"mode": "ok", "gate_file": str(gate)}
    )
    catalog = EdgeVoiceCatalog(store.paths.data)
    provider = EdgeTTS(runtime, catalog, allowed=True, book=view.book, chapter=view.chapter)
    try:
        edge_view = store.view(view.chapter, 0, provider.capabilities())
        reqs = [
            provider.prepare(edge_view.lines[i].spoken, edge_view.lines[i].voice, "")
            for i in range(3)
        ]
        # Occupy the single worker slot with line 0 (blocked on gate).
        t0 = threading.Thread(
            target=lambda: provider.synthesize(reqs[0], tmp_path / "p0.partial", Event())
        )
        t0.start()
        deadline = time.monotonic() + 5
        while not _read_log(log_path):
            time.sleep(0.02)
            assert time.monotonic() < deadline

        # Enqueue prefetch (line 1, priority 2) first, then playback (line 2, priority 0).
        def run_prefetch():
            with edge_priority(PRIORITY_PREFETCH):
                provider.synthesize(reqs[1], tmp_path / "p1.partial", Event())

        def run_playback():
            with edge_priority(PRIORITY_PLAYBACK):
                provider.synthesize(reqs[2], tmp_path / "p2.partial", Event())

        t_pre = threading.Thread(target=run_prefetch)
        t_play = threading.Thread(target=run_playback)
        t_pre.start()
        time.sleep(0.1)
        t_play.start()
        time.sleep(0.1)

        # Release gate; playback (line 2) must run before prefetch (line 1).
        gate.write_text("go", encoding="utf-8")
        for t in (t0, t_pre, t_play):
            t.join(timeout=8)
            assert not t.is_alive()

        sent_texts = [e["text"] for e in _read_log(log_path) if e["kind"] == "synthesize"]
        assert sent_texts == [
            edge_view.lines[0].spoken,
            edge_view.lines[2].spoken,
            edge_view.lines[1].spoken,
        ]
    finally:
        runtime.close()


def test_edge_twenty_jumps_with_delayed_responses(app, tmp_path):
    paths = Paths(tmp_path / "jumps")
    gate = tmp_path / "jump_gate.txt"
    runtime, _, _ = _make_runtime(tmp_path, {"mode": "ok", "gate_file": str(gate)})
    win = ReaderWindow(paths, force_mock=True)
    win.edge_runtime.close()
    win.edge_runtime = runtime
    try:
        win.demo()
        wait(app, lambda: bool(win.view))
        win.edge_voice(False)
        wait(app, lambda: win.view and win.provider.capabilities().provider == EDGE_PROVIDER)
        chapter_id = win.view.chapter
        win._authorize_edge_chapter(win.view)

        arrivals = []
        win.play_artifact = lambda artifact: arrivals.append(artifact.request.text)
        win.start()
        for i in range(20):
            win.seek(i % len(win.view.lines))
        expected_index = win.index
        expected_text = win.view.lines[expected_index].spoken
        gate.write_text("go", encoding="utf-8")
        wait(app, lambda: bool(arrivals))
        time.sleep(0.2)
        app.processEvents()
        assert arrivals == [expected_text]
        assert win.index == expected_index and win.view.chapter == chapter_id
    finally:
        win.close()


def test_edge_roles_retired_voice_speed_style_catalog_and_export(app, tmp_path):
    paths = Paths(tmp_path / "角色 书库")
    runtime, cfg_path, log_path = _make_runtime(tmp_path, {"mode": "ok", "frames": 10})
    win = ReaderWindow(paths, force_mock=True)
    win.edge_runtime.close()
    win.edge_runtime = runtime
    try:
        text = "夜雨微凉。\n少女阿宁说：“灯亮了。”\n少年林渊答：“我们走。”\n少女阿宁说：“小心台阶。”"
        win.add_draft(read_draft(text.encode("utf-8"), "雨夜"))
        wait(app, lambda: bool(win.view))
        win.edge_voice(False)
        wait(app, lambda: win.view and win.provider.capabilities().provider == EDGE_PROVIDER)

        # Narrator uses narrator preset; female & male roles get gender-matched presets.
        by_speaker = {line.speaker: line.voice for line in win.view.lines}
        ids = {name: cid for cid, name in win.view.characters}
        assert by_speaker["narrator"] == "zh-CN-XiaoxiaoNeural"
        assert by_speaker[ids["阿宁"]] in ("zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural")
        assert by_speaker[ids["林渊"]] in ("zh-CN-YunxiNeural", "zh-CN-YunjianNeural")

        # Manually bind 阿宁 to zh-CN-XiaoyiNeural and wait for revision reload.
        aning_idx = next(i for i, l in enumerate(win.view.lines) if l.speaker == ids["阿宁"])
        old_rev = win.view.revision
        win.seek(aning_idx)
        win.voice.setCurrentIndex(win.voice.findData("zh-CN-XiaoyiNeural"))
        win.change_voice()
        wait(
            app,
            lambda: (
                win.view
                and win.view.revision > old_rev
                and win.view.lines[aning_idx].voice == "zh-CN-XiaoyiNeural"
            ),
        )

        # Export before full cache fails clearly without leaving output dir.
        missing_out = tmp_path / "incomplete_export"
        win.chapter_task(missing_out)
        wait(app, lambda: "尚有未生成" in win.note.text())
        assert not missing_out.exists()

        # Authorize and pre-generate entire chapter.
        win._authorize_edge_chapter(win.view)
        win.generate()
        wait(app, lambda: "预生成本章完成" in win.note.text())
        sends_after_gen = len([e for e in _read_log(log_path) if e["kind"] == "synthesize"])
        unique_pairs = {(l.spoken, l.voice) for l in win.view.lines}
        assert sends_after_gen == len(unique_pairs) == 6

        # Speed change 1.0 -> 1.5, catalog refresh, editing Edge-unsupported style: ZERO extra sends!
        win.speed.setCurrentText("1.5")
        refreshed = runtime.list_voices(Event())
        win.edge_catalog.update_catalog(refreshed)
        # Edit style on line 1 (阿宁 dialogue) — Edge has instruct=False so key unchanged!
        line1 = win.view.lines[aning_idx]
        win.repo.call("save_edit", win.view, line1.start, line1.end, line1.speaker, "轻柔低语")
        win.load_page(win.view.page, line1.start)
        wait(app, lambda: win.view and win.view.lines[aning_idx].style == "轻柔低语")
        # Re-running pre-generate hits cache for ALL lines -> zero extra synthesis!
        win.generate()
        wait(app, lambda: "预生成本章完成" in win.note.text())
        assert len([e for e in _read_log(log_path) if e["kind"] == "synthesize"]) == sends_after_gen

        # Export complete chapter and verify manifest + WAV frames.
        out_dir = tmp_path / "完整 Edge 导出"
        win.chapter_task(out_dir)
        wait(app, lambda: (out_dir / "manifest.json").exists())
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        segs = manifest["segments"]
        assert manifest["complete"] is True
        assert [s["start_cp"] for s in segs] == [l.start for l in win.view.lines]
        assert {s["origin"] for s in segs} == {"cloud"}
        assert {s["provider"] for s in segs} == {EDGE_PROVIDER}
        assert [s["voice"] for s in segs] == [l.voice for l in win.view.lines]
        with wave.open(str(out_dir / "chapter.wav"), "rb") as wav:
            assert wav.getnframes() == sum(s["frames"] for s in segs) == manifest["frames"]

        # Change ONLY 林渊's voice -> narrator and 阿宁 reuse cache!
        linyuan_idx = next(i for i, l in enumerate(win.view.lines) if l.speaker == ids["林渊"])
        new_male = (
            "zh-CN-YunjianNeural"
            if win.view.lines[linyuan_idx].voice != "zh-CN-YunjianNeural"
            else "zh-CN-YunxiNeural"
        )
        win.seek(linyuan_idx)
        win.voice.setCurrentIndex(win.voice.findData(new_male))
        win.change_voice()
        wait(app, lambda: win.view and win.view.lines[linyuan_idx].voice == new_male)
        win.generate()
        wait(app, lambda: "预生成本章完成" in win.note.text())
        sends_after_role_change = len([e for e in _read_log(log_path) if e["kind"] == "synthesize"])
        assert sends_after_role_change == sends_after_gen + 1

        # Simulate retiring zh-CN-XiaoyiNeural in catalog refresh!
        win.edge_catalog.update_catalog(
            [
                {"short_name": "zh-CN-XiaoxiaoNeural", "locale": "zh-CN", "gender": "Female"},
                {"short_name": "zh-CN-YunxiNeural", "locale": "zh-CN", "gender": "Male"},
                {"short_name": "zh-CN-YunjianNeural", "locale": "zh-CN", "gender": "Male"},
            ]
        )
        # Old cached sentence for 阿宁 STILL plays offline from cache with original voice ID!
        retired_view = win.repo.call("view", win.view.chapter, 0, win.provider.capabilities())
        aning_line = retired_view.lines[aning_idx]
        assert aning_line.voice == "zh-CN-XiaoyiNeural" and aning_line.substitute is True
        cached = win.library.audio(retired_view, aning_line, win.provider, Event())
        assert cached.request.voice == "zh-CN-XiaoyiNeural"
        assert len([e for e in _read_log(log_path) if e["kind"] == "synthesize"]) == sends_after_role_change

        # Uncached sentence with retired voice refuses to synthesize until replacement chosen!
        with pytest.raises(EdgeVoiceUnavailableError, match="已不在当前可用列表中"):
            req = win.provider.prepare("从未缓存的新台词。", "zh-CN-XiaoyiNeural", "")
            win.provider.synthesize(req, tmp_path / "x.partial", Event())

        # Custom voice ID persists across catalog refresh.
        win.edge_catalog.add_custom_voice("zh-TW-HsiaoChenNeural")
        win.edge_catalog.update_catalog(refreshed)
        assert "zh-TW-HsiaoChenNeural" in win.edge_catalog.voices()
    finally:
        win.close()

    # Manual binding survives restart!
    reopened = ReaderWindow(paths)
    try:
        wait(app, lambda: bool(reopened.view))
        ids2 = {name: cid for cid, name in reopened.view.characters}
        aning_voices = [l.voice for l in reopened.view.lines if l.speaker == ids2["阿宁"]]
        assert aning_voices and all(v == "zh-CN-XiaoyiNeural" for v in aning_voices)
    finally:
        reopened.close()


@pytest.mark.parametrize("action", ["play", "pregenerate"])
def test_edge_ui_first_cache_miss_prompts_and_authorizes(app, tmp_path, monkeypatch, action):
    paths = Paths(tmp_path / "consent")
    runtime, _, log_path = _make_runtime(tmp_path, {"mode": "ok"})
    win = ReaderWindow(paths, force_mock=True)
    win.edge_runtime.close()
    win.edge_runtime = runtime
    try:
        win.demo()
        wait(app, lambda: bool(win.view))
        win.edge_voice(False)
        wait(app, lambda: win.view and win.provider.capabilities().provider == EDGE_PROVIDER)

        prompts = []
        played = []
        win.play_artifact = lambda artifact: played.append(artifact.request.text)

        def choose(box):
            prompts.append(box.text())
            assert "微软" in box.text() and "后台预取" in box.text()
            assert box.defaultButton().text() == "仅用缓存"
            choice = "仅用缓存" if len(prompts) == 1 else "允许本章发送到微软合成"
            next(button for button in box.buttons() if button.text() == choice).click()

        monkeypatch.setattr(QMessageBox, "exec", choose)
        win.intent = True
        win.prefetch()
        wait(app, lambda: not win.bg.events)
        assert not prompts and runtime.start_count == 0
        win.stop()

        trigger = win.start if action == "play" else win.generate
        trigger()
        wait(app, lambda: "已保持仅用缓存" in win.note.text())
        assert len(prompts) == 1
        assert not win.provider.is_authorized()
        assert _read_log(log_path) == []

        trigger()
        if action == "play":
            wait(app, lambda: bool(played))
            assert played == [win.view.lines[0].spoken]
        else:
            wait(app, lambda: "预生成本章完成" in win.note.text())
            sends = [entry["text"] for entry in _read_log(log_path)]
            assert set(sends) == {line.spoken for line in win.view.lines}
        assert len(prompts) == 2
        assert win.provider.is_authorized()
    finally:
        win.close()


def test_edge_cache_distinguishable_from_local_qwen(book, tmp_path):
    store, library, _, view = book
    runtime, _, _ = _make_runtime(tmp_path, {"mode": "ok"})
    catalog = EdgeVoiceCatalog(store.paths.data)
    edge = EdgeTTS(runtime, catalog, allowed=True, book=view.book, chapter=view.chapter)
    local = LocalTTS(
        {
            "model": MODELS[0],
            "revision": "rev-local-1",
            "origin": "local",
            "voices": ["Vivian", "Dylan"],
            "instruct": False,
        }
    )
    try:
        edge_view = store.view(view.chapter, 0, edge.capabilities())
        local_view = store.view(view.chapter, 0, local.capabilities())
        edge_req = edge.prepare(edge_view.lines[0].spoken, edge_view.lines[0].voice, "")
        local_req = local.prepare(local_view.lines[0].spoken, local_view.lines[0].voice, "")
        assert edge_req.key != local_req.key
        assert edge_req.origin == "cloud" and local_req.origin == "local"
        art = library.audio(edge_view, edge_view.lines[0], edge, Event())
        assert art.request.origin == "cloud"
        # Local cache miss confirms zero cross-contamination.
        with pytest.raises(ValueError, match="尚有未生成"):
            library.audio(local_view, local_view.lines[0], local, Event(), cache_only=True)
    finally:
        runtime.close()


def test_edge_direct_menu_grant_uses_edge_voices_across_pages(app, tmp_path, monkeypatch):
    runtime, _, log_path = _make_runtime(tmp_path, {"mode": "ok"})
    win = ReaderWindow(Paths(tmp_path / "直接授权"), force_mock=True)
    win.edge_runtime.close()
    win.edge_runtime = runtime
    played = []
    monkeypatch.setattr(win, "play_artifact", lambda artifact: played.append(artifact.request))

    def accept_consent(box):
        assert box.defaultButton().text() == "仅用缓存"
        next(b for b in box.buttons() if b.text() == "允许本章发送到微软合成").click()

    monkeypatch.setattr(QMessageBox, "exec", accept_consent)
    try:
        source = "甲。\n" + "　" * 16000 + "\n乙。"
        win.add_draft(read_draft(source.encode(), "翻页"))
        wait(app, lambda: bool(win.view))
        assert isinstance(win.provider, MockTTS)
        next(a for a in win.voices_menu.actions() if a.text() == "允许本章使用 Edge 合成…").trigger()
        wait(app, lambda: bool(win.view))
        provider = win.provider
        assert provider.is_authorized()
        assert win.view.lines[win.index].voice == provider.catalog.narrator()
        assert _read_log(log_path) == []
        win.start()
        wait(app, lambda: len(played) == 1)
        win.advance()
        wait(app, lambda: len(played) == 2)
        assert win.view.page == win.view.page_count - 1
        assert win.provider is provider
        assert [(r.text, r.voice) for r in played] == [
            ("甲。", provider.catalog.narrator()), ("乙。", provider.catalog.narrator())
        ]
        win.switch_voice(MockTTS())
        assert not provider.is_authorized()
        # Switch again while the previous source's page is still loading.
        assert win.view is None
        win.edge_voice(False)
        wait(app, lambda: bool(win.view))
        assert win.view.lines[win.index].voice == provider.catalog.narrator()
        assert not win.provider.is_authorized()
    finally:
        win.close()


@pytest.mark.parametrize("wrong_scope", ["book", "chapter"])
def test_edge_grant_only_allows_cache_misses_for_its_book_and_chapter(book, tmp_path, wrong_scope):
    store, library, _, view = book
    runtime, _, log_path = _make_runtime(tmp_path, {"mode": "ok"})
    catalog = EdgeVoiceCatalog(store.paths.data)
    scope = {"book": view.book, "chapter": view.chapter}
    scope[wrong_scope] = "另一" + wrong_scope
    provider = EdgeTTS(runtime, catalog, allowed=True, **scope)
    try:
        page = store.view(view.chapter, 0, provider.capabilities())
        with pytest.raises(EdgeAuthorizationRequired):
            library.audio(page, page.lines[0], provider, Event())
        assert runtime.start_count == 0
        assert not _read_log(log_path) and not store.entries()
        assert not list(store.paths.cache.glob("*.partial"))
    finally:
        runtime.close()


@pytest.mark.skipif(
    os.environ.get("NOVEL_TTS_TEST_EDGE_ONLINE") != "1",
    reason="设置 NOVEL_TTS_TEST_EDGE_ONLINE=1 以向微软合成固定测试短句",
)
def test_edge_live_synthesis_and_offline_export(book, tmp_path):
    store, library, _, _ = book
    draft = read_draft("窗前的风铃响了。".encode(), "在线验收")
    book_id = store.add_book(draft, draft.boundaries, Event())
    chapter = store.toc(book_id)[0][0]
    catalog = EdgeVoiceCatalog(store.paths.data)
    runtime = EdgeWorkerRuntime()
    provider = EdgeTTS(runtime, catalog, allowed=True, book=book_id, chapter=chapter)
    page = store.view(chapter, 0, provider.capabilities())
    try:
        assert library.chapter(page, provider, Event()) == 1
    finally:
        provider.revoke()
        runtime.close()
    offline = EdgeWorkerRuntime(component_dir=tmp_path / "无组件")
    try:
        cached = EdgeTTS(offline, catalog)
        artifact = library.audio(page, page.lines[0], cached, Event())
        out = library.chapter(page, cached, Event(), tmp_path / "离线导出")
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["segments"][0]["sha256"] == artifact.sha256
        assert manifest["segments"][0]["origin"] == "cloud"
        with wave.open(str(out / "chapter.wav")) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
            assert wav.getnframes() == artifact.frames > 0
            assert any(wav.readframes(artifact.frames))
        assert offline.start_count == 0
    finally:
        offline.close()


def test_edge_catalog_keeps_previous_state_on_failed_save(tmp_path, monkeypatch):
    catalog = EdgeVoiceCatalog(tmp_path)
    voice = catalog.add_custom_voice("zh-HK-HiuMaanNeural")
    catalog.set_narrator(voice)
    saved = catalog.path.read_bytes()
    previous = catalog.all_entries()

    def disk_full(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", disk_full)
    with pytest.raises(OSError, match="disk full"):
        catalog.update_catalog([{"ShortName": "en-US-AriaNeural"}])
    assert catalog.all_entries() == EdgeVoiceCatalog(tmp_path).all_entries() == previous
    assert catalog.path.read_bytes() == saved and catalog.narrator() == voice
    assert not list(tmp_path.glob("*.tmp"))


def test_edge_corrupt_cache_export_preserves_prior_files(book, tmp_path):
    store, library, _, original = book
    runtime, _, log = _make_runtime(tmp_path, {"mode": "ok"})
    provider = EdgeTTS(runtime, EdgeVoiceCatalog(store.paths.data), allowed=True,
                       book=original.book, chapter=original.chapter)
    try:
        page = store.view(original.chapter, 0, provider.capabilities())
        library.chapter(page, provider, Event())
        saved = library.chapter(page, provider, Event(), tmp_path / "已导出")
        files = {p.name: p.read_bytes() for p in saved.iterdir()}
        sent = _read_log(log)
        artifact = library.audio(page, page.lines[-1], provider, Event())
        artifact.path.write_bytes(b"RIFF")

        with pytest.raises(ValueError, match="请先预生成"):
            library.chapter(page, provider, Event(), tmp_path / "新导出")
        with pytest.raises(FileExistsError):
            library.chapter(page, provider, Event(), saved)
        assert not (tmp_path / "新导出").exists() and not list(tmp_path.glob(".novel-export-*"))
        assert files == {p.name: p.read_bytes() for p in saved.iterdir()}
        assert _read_log(log) == sent
    finally:
        runtime.close()


def test_edge_playback_and_prefetch_share_one_synthesis(book, app, tmp_path, monkeypatch):
    store, _, _, view = book
    repo = Repository(lambda: Store(store.paths))
    library = Library(store.paths, repo)
    runtime, _, log = _make_runtime(tmp_path, {"mode": "ok"})
    provider = EdgeTTS(runtime, EdgeVoiceCatalog(store.paths.data), allowed=True,
                       book=view.book, chapter=view.chapter)
    barrier = threading.Barrier(2)
    prepare = provider.prepare

    def prepare_together(*args):
        request = prepare(*args)
        barrier.wait(timeout=5)
        return request

    monkeypatch.setattr(provider, "prepare", prepare_together)
    try:
        page = repo.call("view", view.chapter, 0, provider.capabilities())
        with ThreadPoolExecutor(2) as pool:
            tasks = [
                pool.submit(library.audio, page, page.lines[0], provider, Event(), prefetch=prefetch)
                for prefetch in (True, False)
            ]
            first, second = [task.result(timeout=10) for task in tasks]
        assert first == second and len(repo.call("entries")) == 1
        assert [entry["text"] for entry in _read_log(log)] == [page.lines[0].spoken]
    finally:
        runtime.close()
        repo.close()


def test_edge_component_unit_tests_in_worker_environment():
    subprocess.run(
        [str(_edge_python()), "-m", "unittest", "discover", "-s", "tests"],
        cwd=REPO_ROOT / "edge_component",
        check=True,
        timeout=30,
    )


def test_windows_preview_includes_edge_with_target_interpreter(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    import assemble_windows_preview as preview

    stage, out = tmp_path / "stage", tmp_path / "out"
    stage.mkdir()
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"portable Windows interpreter")
    archive = stage / "cpython.tar.gz"
    with tarfile.open(archive, "w:gz") as runtime:
        runtime.add(interpreter, arcname="python/python.exe")
    monkeypatch.setattr(preview, "DIGEST", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(sys, "argv", ["assemble_windows_preview", "--stage", str(stage), "--out", str(out)])
    # Wheel installation and the real worker handshake run in the native staging test.
    def skip_uv(command, **kwargs):
        assert command[0] == "uv"

    monkeypatch.setattr(subprocess, "run", skip_uv)
    preview.main()
    with zipfile.ZipFile(next(out.glob("*.zip"))) as bundle:
        component = "NovelTTSReader/edge_component/"
        assert bundle.read(component + "python/python.exe") == interpreter.read_bytes()
        assert json.loads(bundle.read(component + "component.json"))["name"] == "novel-tts-edge"


def test_edge_staged_component_and_library_replacement(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from build_desktop import stage_edge_component, verify_staged_edge_component
    finally:
        sys.path.pop(0)

    target_platform = "windows" if os.name == "nt" else "linux"
    bundle = tmp_path / "中文 路径 发行包"
    bundle.mkdir()
    comp = stage_edge_component(REPO_ROOT, bundle, target_platform)
    verify_staged_edge_component(comp)
    with monkeypatch.context() as portable:
        portable.delenv("NOVEL_TTS_EDGE_COMPONENT", raising=False)
        portable.setattr(sys, "executable", str(bundle / "python/python.exe"))
        assert locate_edge_component().root == comp
    assert (comp / "README-REPLACE.md").is_file()
    assert (comp / "uv.lock").is_file()
    licenses = comp / "licenses"
    license_files = [p for p in licenses.rglob("*") if p.is_file()]
    edge_license = next(licenses.glob("edge_tts-*.dist-info/**/LICENSE"))
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in edge_license.read_text(encoding="utf-8")
    gpl = (licenses / "GPL-3.0.txt").read_text(encoding="utf-8")
    assert gpl == (REPO_ROOT / "edge_component/licenses/GPL-3.0.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in gpl and "END OF TERMS AND CONDITIONS" in gpl
    assert any("miniaudio" in str(p) for p in license_files)

    # Verify LGPLv3 library replacement: modify edge_tts in libs/ and confirm worker loads it!
    marker_file = comp / "libs" / "edge_tts" / "__init__.py"
    assert marker_file.is_file()
    marker_file.write_text(
        marker_file.read_text(encoding="utf-8") + "\nNOVEL_TTS_REPLACED_MARKER = 'ok'\n",
        encoding="utf-8",
    )
    py_exe = comp / "python/python.exe" if os.name == "nt" else comp / "python/bin/python3"
    env = dict(os.environ, PYTHONUTF8="1")
    env["PYTHONPATH"] = os.pathsep.join([str(comp / "libs"), str(comp)])
    out = subprocess.check_output(
        [str(py_exe), "-u", "-c", "import edge_tts; print(edge_tts.NOVEL_TTS_REPLACED_MARKER)"],
        env=env,
        text=True,
        encoding="utf-8",
    )
    assert out.strip() == "ok"
    verify_staged_edge_component(comp)
