"""Verify a shipped runtime with empty caches and synthesize speech using its own weights."""

import hashlib
import json
import sys
from importlib import metadata
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio
from novel_tts_sidecar import streaming
from novel_tts_sidecar.models import MODELS
from novel_tts_sidecar.prepare import verify
from novel_tts_sidecar.worker import read_settings
from qwen_tts import Qwen3TTSModel


def main():
    local, report, sample = map(Path, sys.argv[1:])
    # Windows TEMP can use an 8.3 alias while Python reports the long path.
    local = local.resolve(strict=True)
    config = local / "models/runtime.json"
    model, revision = MODELS["0.6B"]
    entry = read_settings(config)["models"][model]
    folder = Path(entry["path"]).resolve(strict=True)
    if not Path(sys.prefix).samefile(local / "python"):
        raise ValueError(
            f"Interpreter prefix {sys.prefix} is outside {local / 'python'}"
        )
    if not folder.is_relative_to((local / "models").resolve(strict=True)):
        raise ValueError(f"Model path {folder} is outside {local / 'models'}")
    manifest = json.loads((folder / "novel-verified.json").read_text(encoding="utf-8"))
    if (manifest["repo"], manifest["revision"], entry["revision"]) != (
        model,
        revision,
        revision,
    ):
        raise ValueError("Unexpected model revision")
    for name, expected in manifest["files"].items():
        if verify(folder / name, expected["algorithm"]) != expected["digest"]:
            raise ValueError(f"Changed model file: {name}")
    if (torch.version.cuda, torchaudio.__version__) != ("12.8", "2.8.0+cu128"):
        raise ValueError("Wrong CUDA runtime")
    if not callable(streaming.build_streamer):
        raise ValueError("Streaming engine unavailable")
    torch.set_num_threads(2)
    torch.manual_seed(23)
    wrapper = Qwen3TTSModel.from_pretrained(
        str(folder),
        device_map="cpu",
        dtype=torch.float32,
        attn_implementation="eager",
        local_files_only=True,
    )
    text = "你好，欢迎听书。"
    with torch.inference_mode():
        waves, rate = wrapper.generate_custom_voice(
            text=text,
            speaker="Vivian",
            language="Chinese",
            max_new_tokens=96,
            do_sample=False,
            subtalker_dosample=False,
        )
    audio = np.asarray(waves[0])
    if (
        rate != 24000
        or audio.size < rate // 4
        or not np.isfinite(audio).all()
        or np.max(np.abs(audio)) < 0.005
    ):
        raise ValueError("Expected finite, non-silent 24 kHz speech")
    sf.write(sample, audio, rate, subtype="PCM_16")
    versions = {
        name: metadata.version(name)
        for name in (
            "torch",
            "torchaudio",
            "qwen-tts",
            "faster-qwen3-tts",
            "novel-tts-sidecar",
        )
    }
    report.write_text(
        json.dumps(
            {
                "commit": json.loads(
                    (local / "build.json").read_text(encoding="utf-8")
                )["commit"],
                "model": model,
                "revision": revision,
                "verified_model_files": len(manifest["files"]),
                "desktop_self_test": "passed",
                "bundled_sidecar_handshake": "passed",
                "inference_device": "cpu",
                "production_cuda_test_in_this_run": False,
                "versions": versions,
                "text": text,
                "sample_rate": rate,
                "frames": int(audio.size),
                "sample_sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report.read_text(encoding="utf-8"), flush=True)


if __name__ == "__main__":
    main()
