import array
import hashlib
import json
import math
import shutil
import sys
import wave

from novel_tts.domain.models import (
    Cancelled,
    Capabilities,
    ProviderError,
    SynthesisRequest,
)
from novel_tts.infrastructure.audio import RATE, inspect_wav
from novel_tts.infrastructure.paths import RESOURCES

VOICES = ("旁白", "松墨", "星禾")


def tone(path, voice, length=0.42):
    count = int(RATE * length)
    freq = 240 + 70 * (sum(voice.encode()) % 5)
    samples = array.array(
        "h",
        (
            int(
                3600
                * min(1, i / 240, (count - i) / 240)
                * math.sin(i * freq * 2 * math.pi / RATE)
            )
            for i in range(count)
        ),
    )
    samples.extend([0] * (RATE // 16))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())


class MockTTS:
    def __init__(self, scenario=None):
        self.scenario = scenario
        manifest = json.loads(
            (RESOURCES / "samples/manifest.json").read_text(encoding="utf-8")
        )
        self.clips = {(row["text"], row["voice"]): row for row in manifest["clips"]}

    def capabilities(self):
        return Capabilities("mock", "sample-v1", "offline", "1", VOICES)

    def prepare(self, text, voice, style=""):
        item = self.clips.get((text, voice))
        return SynthesisRequest(
            text,
            "mock",
            "sample-v1",
            item["sha256"] if item else "tone-1",
            voice,
            "mock_fixture" if item else "mock_tone",
        )

    def synthesize(self, request, destination, cancel):
        if self.scenario in ("slow", "out_of_order", "late_result_after_cancel"):
            if cancel.wait(0.85 if sum(request.text.encode()) % 2 else 0.12):
                raise Cancelled()
        if cancel.is_set():
            raise Cancelled()
        if self.scenario == "timeout":
            raise ProviderError("模拟读取超时", False, "unknown")
        if self.scenario == "corrupt":
            destination.write_bytes(b"RIFF")
        elif request.origin == "mock_fixture":
            item = self.clips[(request.text, request.voice)]
            clip = RESOURCES / "samples" / item["file"]
            if hashlib.sha256(clip.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("样本摘要无效")
            shutil.copyfile(clip, destination)
        else:
            tone(destination, request.voice)
        return inspect_wav(destination, request)
