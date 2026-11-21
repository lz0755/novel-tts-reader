import sys
from pathlib import Path
from threading import Event

from novel_tts.providers.local import LocalTTS
from novel_tts.ui.local import ProcessHost
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
        info = clients[0].load("mock", Event())
        provider = LocalTTS(info, clients[0])
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
