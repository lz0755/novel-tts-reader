import io
import math
import sys
import time
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from novel_tts.application.library import Library
from novel_tts.domain.models import Cancelled, ProviderError
from novel_tts.infrastructure.paths import Paths
from novel_tts.providers.local import LocalTTS
from novel_tts.providers.mock import MockTTS
from novel_tts.ui.local import ProcessHost
from novel_tts.ui.pcm import PcmPlayback, RATE
from novel_tts.ui.window import ReaderWindow
from test_mock_flow import wait


def test_stop_while_first_chunk_is_playing_discards_late_pcm(app, tmp_path, monkeypatch):
    class SlowStream(MockTTS):
        can_stream = True

        def synthesize(self, req, destination, cancel, on_pcm):
            on_pcm(b"\x00\x00" * 2400)
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if cancel.wait(.04):
                    raise Cancelled()
            raise AssertionError("Playback did not cancel synthesis")

    # Run every desktop layer up to the sound device, including on headless CI.
    device = SimpleNamespace(isFormatSupported=lambda fmt: True)
    monkeypatch.setattr("novel_tts.ui.player.QMediaDevices",
                        SimpleNamespace(defaultAudioOutput=lambda: device))
    monkeypatch.setattr(PcmPlayback, "play", lambda self: None)
    win = ReaderWindow(Paths(tmp_path))
    try:
        win.provider = SlowStream()
        win.demo()
        wait(app, lambda: bool(win.view))
        win.start()
        wait(app, lambda: isinstance(win.media, PcmPlayback) and win.media.received == 2400)
        key, ticket = win.current_key, win.stream_ticket
        assert not (win.paths.cache / (key + ".wav")).exists()
        win.stop()
        win.pcm_received.emit(ticket, b"\x00\x00")
        wait(app, lambda: not win.callbacks)
        assert win.media is None
        assert not list(win.paths.cache.glob("*.partial"))
        assert not (win.paths.cache / (key + ".wav")).exists()
    finally:
        win.close()


@pytest.mark.parametrize("fails", [False, True], ids=["keep-playing", "shared-failure"])
def test_playback_joins_prefetch_and_keeps_its_prefix(monkeypatch, fails):
    library = Library(None, None)
    provider = Mock(can_stream=True)
    provider.prepare.return_value.key = "sentence"
    view = SimpleNamespace(book="book", revision=1)
    line = SimpleNamespace(spoken="渡船靠岸了。", voice="vivian", style="")
    first, joined, release = Event(), Event(), Event()
    prefetched, playing = Event(), Event()
    chunks = []
    artifact = object()

    def generate(view, req, provider, cancelled, cache_only, publish):
        publish(b"first")
        first.set()
        assert release.wait(5) and not cancelled.is_set()
        if fails:
            raise ProviderError("interrupted stream")
        publish(b"second")
        return artifact

    def receive(chunk):
        chunks.append(chunk)
        joined.set()

    synth = Mock(side_effect=generate)
    monkeypatch.setattr(library, "_audio", synth)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(library.audio, view, line, provider, prefetched, prefetch=True)
        assert first.wait(5)
        follower = pool.submit(library.audio, view, line, provider, playing, on_pcm=receive)
        try:
            assert joined.wait(5)
            prefetched.set()
        finally:
            release.set()
        if fails:
            for future in (owner, follower):
                with pytest.raises(ProviderError, match="interrupted stream"):
                    future.result(5)
            assert chunks == [b"first"]
        else:
            assert follower.result(5) is artifact
            with pytest.raises(Cancelled):
                owner.result(5)
            assert chunks == [b"first", b"second"]
    synth.assert_called_once()
    assert not library.streams
    if fails:
        synth.side_effect = None
        synth.return_value = artifact
        assert library.audio(view, line, provider, playing, on_pcm=receive) is artifact


def test_stream_delivers_authenticated_pcm_before_commit_and_cancels(app, tmp_path):
    host = ProcessHost(None)
    clients = []
    host.ready.connect(clients.append)
    try:
        host.start(sys.executable, engine="mock", cwd=Path(__file__).parents[1] / "sidecar")
        wait(app, lambda: bool(clients))
        client = clients[0]
        cap = client.load("mock", Event())
        body = dict(model="mock", text="雾散了。", voice="mock-voice",
                    stream_session=cap["stream_session"], scenario="stream_slow")
        pcm = []
        active_states = []

        def heard(data):
            pcm.append(data)
            active_states.append(client.call("GET", "health").json()["busy"])

        job, result = client.job("synthesis/jobs", body, Event(), on_pcm=heard)
        with wave.open(io.BytesIO(client.call("GET", f"artifacts/{job}").content)) as wav:
            assert b"".join(pcm) == wav.readframes(wav.getnframes())
            assert wav.getnframes() == result["frames"]
        assert active_states[0] and len(pcm) > 1

        event = Event()
        with pytest.raises(Cancelled):
            client.job("synthesis/jobs", body, event, on_pcm=lambda chunk: event.set())
        until = time.monotonic() + 2
        while client.call("GET", "health").json()["busy"] and time.monotonic() < until:
            time.sleep(.02)
        assert client.call("GET", "health").json()["model_ready"]
        provider = LocalTTS(cap, client)
        dest = tmp_path / "published.wav"
        req = provider.prepare("下一句。", "mock-voice")
        chunks = []
        assert provider.synthesize(req, dest, Event(), chunks.append).frames == 7200
        assert chunks
        fresh = client.load("mock", Event())
        assert fresh["stream_session"] != cap["stream_session"]
        with pytest.raises(ProviderError):
            client.job("synthesis/jobs", body, Event(), on_pcm=pytest.fail)
    finally:
        host.close()


@pytest.mark.parametrize("speed", [0.75, 1.0, 1.5, 2.0])
def test_streaming_tempo_preserves_pitch_and_flushes_tail(app, speed):
    clip = array("h", (round(6000 * math.sin(i * 2 * math.pi * 220 / RATE))
                       for i in range(RATE)))
    live = PcmPlayback(None)
    live.setPlaybackRate(speed)
    live.queue.append(memoryview(clip).cast("B"))
    live.complete = True
    data = bytearray()
    while live.queue or not live.flushed:
        data.extend(live.next_block())
    samples = memoryview(data).cast("h")
    assert abs(len(samples) / RATE - 1 / speed) < 0.04
    crossings = sum(a < 0 <= b for a, b in zip(samples, samples[1:]))
    assert abs(crossings * RATE / len(samples) - 220) < 5
    if speed == 1:
        assert data == clip.tobytes()
    live.deleteLater()


def test_full_chunk_at_eos_and_both_runaway_limits():
    pytest.importorskip("faster_qwen3_tts")
    import numpy as np
    from novel_tts_sidecar.models import InferenceError
    from novel_tts_sidecar.streaming import CACHE_TOKENS, audio_chunks

    streamer = SimpleNamespace(talker_graph=SimpleNamespace(prompt_tokens=50, max_seq_len=CACHE_TOKENS))
    chunk = np.zeros(15360, dtype=np.float32)
    streamer.generate_custom_voice_streaming = lambda **_: iter([
        (chunk, RATE, {"total_steps_so_far": 8, "is_final": False})])
    assert list(audio_chunks(streamer, "雾散了。", "vivian", "", 30))[0] is chunk
    with pytest.raises(InferenceError, match="missing_eos_or_token_cap"):
        list(audio_chunks(streamer, "雾散了。", "vivian", "", 8))
    streamer.talker_graph.prompt_tokens = CACHE_TOKENS - 8
    with pytest.raises(InferenceError, match="missing_eos_or_token_cap"):
        list(audio_chunks(streamer, "雾散了。", "vivian", "", 30))
