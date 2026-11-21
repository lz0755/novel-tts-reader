import base64
import io
import wave
from dataclasses import replace
from threading import Event

import httpx
import pytest

from novel_tts.domain.models import ProviderError
from novel_tts.infrastructure.profiles import DEFAULTS
from novel_tts.providers.cloud_tts import QwenCloudTTS


def raw_wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setparams((2, 2, 12000, 0, "NONE", "not compressed"))
        f.writeframes(b"\0\1" * 2 * 1200)
    return out.getvalue()


@pytest.mark.parametrize("format", ["url", "base64", "binary"])
def test_audio_protocol(book, tmp_path, format):
    store, library, _, view = book
    called = []
    p = replace(
        DEFAULTS["qwen_cloud"],
        endpoint="https://example.com/tts",
        voices=("LongA", "LongB"),
    )
    raw = raw_wav()

    def server(request):
        import json

        called.append(request.method)
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=raw)
        payload = json.loads(request.content)
        assert (
            payload["input"]["voice"] in p.voices
            and "instruction" not in payload["input"]
        )
        if format == "binary":
            return httpx.Response(
                200, content=raw, headers={"Content-Type": "audio/wav"}
            )
        audio = (
            {"url": "https://example.com/wav"}
            if format == "url"
            else {"data": base64.b64encode(raw).decode()}
        )
        return httpx.Response(
            200,
            json={
                "output": {"finish_reason": "stop", "audio": audio},
                "usage": {"characters": 3},
            },
        )

    cloud = QwenCloudTTS(p, "test", transport=httpx.MockTransport(server))
    view = store.view(view.chapter, 0, cloud.capabilities())
    first = view.lines[0]
    request = cloud.prepare(first.spoken, first.voice, "低声")
    assert not request.style
    art = library.audio(view, first, cloud, Event())
    assert 2395 <= art.frames <= 2400 and art.rate == 24000 and art.channels == 1
    count = len(called)
    cloud.allowed.clear()
    assert (
        library.audio(view, first, cloud, Event()).sha256 == art.sha256
        and len(called) == count
    )
    with pytest.raises(ProviderError, match="尚未允许上传"):
        library.audio(view, view.lines[1], cloud, Event())


def test_incomplete_or_corrupt_has_no_success(book):
    store, lib, _, v = book
    p = replace(DEFAULTS["qwen_cloud"], endpoint="https://example.com/tts")
    for raw in [
        {"output": {"finish_reason": "length"}},
        {"output": {"finish_reason": "stop", "audio": {"data": "bad!"}}},
    ]:
        cloud = QwenCloudTTS(
            p,
            "",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw)),
        )
        v = store.view(v.chapter, 0, cloud.capabilities())
        with pytest.raises(ProviderError):
            lib.audio(v, v.lines[0], cloud, Event())
        assert not store.entries()
