import json
import os
import platform
import time
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from .models import MODELS, InferenceError, safe_sections, token_budget


class Runner:
    def __init__(self, settings):
        self.settings = settings
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    def load(self, data):
        import torch
        from qwen_tts import Qwen3TTSModel
        from .attention import select_attention
        from .prepare import verify

        self.model_id = data["model"]
        expected = dict(MODELS.values()).get(self.model_id)
        entry = self.settings.get("models", {}).get(self.model_id)
        if not entry or not expected or entry.get("revision") != expected:
            raise InferenceError("model_not_prepared")
        if not torch.cuda.is_available():
            raise InferenceError("cuda_unavailable")
        folder = Path(entry["path"])
        manifest = json.loads(
            (folder / "novel-verified.json").read_text(encoding="utf-8")
        )
        if (manifest.get("repo"), manifest.get("revision")) != (
            self.model_id,
            expected,
        ) or not manifest.get("files"):
            raise InferenceError("model_manifest_mismatch")
        for name, entry_ in manifest["files"].items():
            if verify(folder / name, entry_["algorithm"]) != entry_["digest"]:
                raise InferenceError("model_digest_mismatch")
        self.instruct = self.model_id == MODELS["1.7B"][0]
        ampere = torch.cuda.get_device_capability(0)[0] >= 8
        torch.backends.cuda.matmul.allow_tf32 = bool(
            ampere and self.settings.get("tf32", True)
        )
        torch.backends.cudnn.allow_tf32 = bool(
            ampere and self.settings.get("tf32", True)
        )
        torch.backends.cudnn.benchmark = bool(
            ampere and self.settings.get("benchmark", True)
        )
        quantization = data.get("quantization", self.settings.get("quantization", "none"))
        if quantization not in ("none", "int4"):
            raise InferenceError("unsupported_quantization")
        dtype = self.settings.get("dtype", "bfloat16")
        attention = self.settings.get("attention", "sdpa")
        if dtype not in ("bfloat16", "float16", "float32") or attention not in (
            "sdpa",
            "eager",
            "flash_attention_2",
        ):
            raise InferenceError("unsupported_runtime_options")
        if dtype == "bfloat16" and not torch.cuda.is_bf16_supported():
            raise InferenceError("bf16_unsupported")
        if quantization == "int4" and (dtype != "bfloat16" or not ampere):
            raise InferenceError("int4_requires_ampere_and_bfloat16")
        start = time.monotonic()
        free, total = torch.cuda.mem_get_info()
        effective_attention = select_attention(attention, dtype, folder)
        self.model = Qwen3TTSModel.from_pretrained(
            str(folder),
            dtype=getattr(torch, dtype),
            device_map=None if quantization == "int4" else "cuda:0",
            local_files_only=True,
            attn_implementation=effective_attention,
        )
        runtime_settings = {
            k: self.settings.get(k)
            for k in ("token_ceiling", "characters_per_second", "length_margin")
        }
        quantized_layers = 0
        if quantization == "int4":
            from .int4 import GROUP_SIZE, quantize_talker

            quantized_layers = quantize_talker(self.model, torch.device("cuda:0"))
            # Also fingerprint the effective weights in older desktop clients.
            runtime_settings.update(quantization="int4", int4_group_size=GROUP_SIZE)
        self.quantization = quantization
        torch.cuda.synchronize()
        load_seconds = time.monotonic() - start
        self.voices = tuple(self.model.get_supported_speakers() or ())
        if not self.voices:
            raise InferenceError("missing_speakers")
        warmup = []
        for text in ("渡口起了雾。", "桥上的灯还亮着。", "我们明天再来。")[
            : min(3, max(2, self.settings.get("warmup", 2)))
        ]:
            begin = time.monotonic()
            self.render(text, self.voices[0], "")
            torch.cuda.synchronize()
            warmup.append(time.monotonic() - begin)
        return dict(
            model=self.model_id,
            revision=expected,
            voices=self.voices,
            instruct=self.instruct,
            origin="local",
            dtype=dtype,
            quantization=quantization,
            quantized_linear_layers=quantized_layers,
            requested_attention=attention,
            attention=effective_attention,
            inference_mode=True,
            tf32=torch.backends.cuda.matmul.allow_tf32,
            benchmark=torch.backends.cudnn.benchmark,
            settings=runtime_settings,
            python=platform.python_version(),
            torch=torch.__version__,
            cuda=torch.version.cuda,
            qwen_tts=metadata.version("qwen-tts"),
            gpu=torch.cuda.get_device_name(),
            vram_total=total,
            vram_free_before=free,
            load_seconds=load_seconds,
            warmup_seconds=warmup,
        )

    def render(self, text, voice, style):
        import numpy as np
        import torch

        budget = token_budget(text, self.settings)
        kwargs = dict(
            text=text, language="Chinese", speaker=voice, max_new_tokens=budget
        )
        if self.instruct and style:
            kwargs["instruct"] = style
        # qwen-tts 0.1.1 hides generation termination in its audio-only API.
        # Inspect the last actual token from the locked talker, before decoding.
        talker = self.model.model.talker
        original = talker.generate
        eos = self.model.model.config.talker_config.codec_eos_token_id
        ending = []

        def observed(*args, **params):
            out = original(*args, **params)
            ending.append(int(out.sequences[0, -1].item()) == int(eos))
            return out

        with torch.inference_mode(), patch.object(talker, "generate", observed):
            wavs, rate = self.model.generate_custom_voice(**kwargs)
        if ending != [True]:
            raise InferenceError("missing_eos_or_token_cap")
        if (
            len(wavs) != 1
            or rate != 24000
            or not len(wavs[0])
            or not np.isfinite(wavs[0]).all()
        ):
            raise InferenceError("invalid_waveform")
        return wavs[0], rate, budget

    def synthesize(self, data, path):
        import soundfile as sf
        import torch

        if data["voice"] not in self.voices or not data["text"].strip():
            raise InferenceError("invalid_voice_or_text")
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        began = time.monotonic()
        frames = 0
        budgets = []
        synth_seconds = 0.0
        with sf.SoundFile(
            str(path),
            mode="w",
            samplerate=24000,
            channels=1,
            subtype="PCM_16",
            format="WAV",
        ) as out:
            for section in safe_sections(data["text"], self.settings):
                before = time.monotonic()
                wave, _, budget = self.render(
                    section, data["voice"], data.get("style", "")
                )
                torch.cuda.synchronize()
                synth_seconds += time.monotonic() - before
                out.write(wave)
                frames += len(wave)
                budgets.append(budget)
        torch.cuda.synchronize()
        elapsed = time.monotonic() - began
        return dict(
            frames=frames,
            rate=24000,
            origin="local",
            quantization=self.quantization,
            pipeline_seconds=elapsed,
            tts_seconds=synth_seconds,
            rtf=synth_seconds / (frames / 24000),
            budgets=budgets,
            applied_instruct=bool(self.instruct and data.get("style")),
            peak_allocated=torch.cuda.max_memory_allocated(),
            peak_reserved=torch.cuda.max_memory_reserved(),
        )
