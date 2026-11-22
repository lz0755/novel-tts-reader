import sys
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from novel_tts.domain.models import ProviderError
from novel_tts.infrastructure.paths import Paths
from novel_tts.providers.local import LocalTTS
from novel_tts.ui.local import ProcessHost
from novel_tts.ui.window import ReaderWindow
from test_mock_flow import wait


def test_qprocess_local_adapter(app, book, tmp_path):
    store, library, _, view = book
    host = ProcessHost(None)
    clients = []
    host.ready.connect(clients.append)
    try:
        host.start(
            sys.executable, engine="mock", cwd=Path(__file__).parents[1] / "sidecar"
        )
        wait(app, lambda: bool(clients))
        client = clients[0]
        body = {"model": "mock", "voice": "mock-voice", "text": "桥上的灯还亮着。"}
        with pytest.raises(ProviderError, match="模型尚未就绪"):
            client.job("synthesis/jobs", body, Event())
        info = client.load("mock", Event())
        before = client.call("GET", "health").json()
        body["stream_session"] = info["stream_session"]
        with client.connection() as http:
            for invalid in ({"text": "\n"}, {"text": "\t　 "}, {"voice": "missing"}):
                ident = uuid4().hex
                response = http.post(
                    "synthesis/jobs", json=body | invalid | {"job_id": ident}
                )
                assert response.status_code == 400
                assert response.json()["error"] == "invalid_voice_or_text"
                assert http.get("jobs/" + ident).status_code == 404
        assert client.call("GET", "health").json() == before
        caps = client.call("GET", "capabilities").json()
        assert caps["stream_session"] == info["stream_session"]
        provider = LocalTTS(info, client)
        current = store.view(view.chapter, 0, provider.capabilities())
        art = library.audio(current, current.lines[0], provider, Event())
        assert art.request.origin == "mock_tone" and art.frames == 7200
        assert art.request.provider == "mock_worker"
        assert (
            library.audio(current, current.lines[0], LocalTTS(info), Event()).sha256
            == art.sha256
        )
    finally:
        host.close()


def test_two_model_effective_style_and_budgets():
    from novel_tts.providers.local import MODELS
    from novel_tts_sidecar.models import safe_sections, token_budget
    from novel_tts.domain.text import boundaries

    txt = "𠮷、👩‍🚀和e\u0301等了三刻钟。" * 30
    settings = {"token_ceiling": 96}
    sections = list(safe_sections(txt, settings))
    assert "".join(sections) == txt and all(
        token_budget(s, settings) <= 96 for s in sections
    )
    positions = boundaries(txt)
    assert all(sum(map(len, sections[:i])) in positions for i in range(len(sections)))
    keys = []
    for model, style in zip(MODELS, [False, True]):
        p = LocalTTS(
            dict(
                model=model,
                revision="fixed",
                voices=["Ryan"],
                origin="local",
                instruct=style,
            )
        )
        req = p.prepare("雨来了。", "Ryan", "低声")
        assert bool(req.style) == style
        keys.append(req.key)
    assert keys[0] != keys[1]


def test_local_cache_uses_effective_attention():
    saved = dict(
        model="model",
        revision="fixed",
        voices=["vivian"],
        origin="local",
        instruct=False,
        attention="sdpa",
        dtype="bfloat16",
    )
    current = saved | {"requested_attention": "sdpa", "inference_mode": True}
    optimized = current | {"attention": "sdpa_expanded_kv"}

    def cache_key(info):
        return LocalTTS(info).prepare("雨来了。", "vivian").key

    assert cache_key(saved) == cache_key(current)
    assert cache_key(optimized) != cache_key(current)


def test_int4_cache_is_distinct_and_rejects_results_after_a_reload(tmp_path):
    info = dict(
        model="model", revision="fixed", voices=["vivian"], origin="local",
        instruct=False, dtype="bfloat16", settings={"token_ceiling": 1536},
    )
    client = Mock()
    int4 = LocalTTS(
        info | dict(quantization="int4", settings=info["settings"] | {
            "quantization": "int4", "int4_group_size": 32,
        }), client,
    )
    request = int4.prepare("雨来了。", "vivian")
    old = LocalTTS(info).prepare("雨来了。", "vivian")
    assert LocalTTS(info | {"quantization": "none"}).prepare("雨来了。", "vivian") == old
    assert request.key != old.key
    assert LocalTTS(int4.info).prepare("雨来了。", "vivian") == request
    client.job.return_value = ("job", {"origin": "local", "quantization": "none"})
    dest = tmp_path / "stale.wav"
    with pytest.raises(ProviderError, match="量化已切换"):
        int4.synthesize(request, dest, Event())
    assert not dest.exists()
    client.call.assert_not_called()


def test_quantization_menu_persists_selection_before_loading(app, tmp_path):
    paths = Paths(tmp_path)
    for expected in ("使用 runtime.json", "INT4"):
        window = ReaderWindow(paths, force_mock=True)
        try:
            menu = next(
                a for a in window.voices_menu.actions() if a.text() == "本地权重量化"
            ).menu()
            actions = {a.text(): a for a in menu.actions()}
            assert [k for k, a in actions.items() if a.isChecked()] == [expected]
            actions["INT4"].trigger()
            assert window.prefs.value("local_quantization") == "int4"
            assert window.host.process is None
        finally:
            window.close()
