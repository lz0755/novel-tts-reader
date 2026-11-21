"""One-time asset authoring. Install espeakng-loader==0.2.4 separately and provide ffmpeg."""

import ctypes as c
import hashlib
import json
import subprocess
import tempfile
import wave
from pathlib import Path

import espeakng_loader

ROOT = Path(__file__).resolve().parents[1] / "src/novel_tts/resources"
VOICES = (("旁白", "cmn", 44, 155), ("松墨", "cmn", 33, 143), ("星禾", "cmn", 63, 162))


def main():
    from novel_tts.domain.normalize import spoken_form
    from novel_tts.domain.annotations import MockLLM
    from novel_tts.domain.models import AnnotationRequest
    from novel_tts.domain.text import parents

    lib = c.CDLL(espeakng_loader.get_library_path())
    path = str(Path(espeakng_loader.get_data_path()).parent).encode()
    rate = lib.espeak_Initialize(2, 0, path, 0)
    assert rate > 0
    chunks = []

    @c.CFUNCTYPE(c.c_int, c.POINTER(c.c_short), c.c_int, c.c_void_p)
    def collect(samples, n, _events):
        if n:
            chunks.append(c.string_at(samples, 2 * n))
        return 0

    lib.espeak_SetSynthCallback(collect)
    source = (ROOT / "demo.txt").read_text(encoding="utf-8")
    targets = parents(source)
    req = AnnotationRequest("demo", "two-chapters", "1", 0, "sample", targets)
    result = MockLLM().analyze_chunk(req)
    voice_for = {c.ref: 1 if c.name == "林渊" else 2 for c in result.candidates}
    folder = ROOT / "samples"
    folder.mkdir(exist_ok=True)
    clips = {}
    with tempfile.TemporaryDirectory() as tmp:
        for p, (_, parts) in zip(targets, result.lines):
            for part in parts:
                if part.speaker == "skip":
                    continue
                text = spoken_form(p.text[part.start : part.end])
                label, voice, pitch, speed = VOICES[voice_for.get(part.speaker, 0)]
                if (text, label) in clips:
                    continue
                assert lib.espeak_SetVoiceByName(voice.encode()) == 0
                lib.espeak_SetParameter(1, speed, 0)
                lib.espeak_SetParameter(3, pitch, 0)
                chunks.clear()
                data = text.encode()
                assert (
                    lib.espeak_Synth(data, len(data) + 1, 0, 1, 0, 1, None, None) == 0
                )
                lib.espeak_Synchronize()
                with wave.open(str(Path(tmp) / "clip.wav"), "wb") as wav:
                    wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                    wav.writeframes(b"".join(chunks))
                filename = f"clip-{len(clips):02d}.wav"
                out = folder / filename
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(Path(tmp) / "clip.wav"),
                        "-af",
                        "volume=0.65",
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        str(out),
                    ],
                    check=True,
                )
                clips[text, label] = dict(
                    text=text,
                    voice=label,
                    file=filename,
                    sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
                )
    manifest = dict(
        version=1,
        origin="mock_fixture",
        attribution="原创样书；eSpeak NG 普通话离线生成的固定合成语音。",
        clips=list(clips.values()),
    )
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
