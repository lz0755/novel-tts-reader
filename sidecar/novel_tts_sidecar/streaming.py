"""CUDA-graph CustomVoice generation with the pinned faster-qwen3-tts API."""

import numpy as np
import torch
from faster_qwen3_tts import FasterQwen3TTS
from faster_qwen3_tts.predictor_graph import PredictorGraph
from faster_qwen3_tts.talker_graph import TalkerGraph

from .models import InferenceError


CHUNK_STEPS = 8
CACHE_TOKENS = 2048


class CheckedTalkerGraph(TalkerGraph):
    def prefill_kv(self, past_key_values):
        self.prompt_tokens = super().prefill_kv(past_key_values)
        return self.prompt_tokens


def build_streamer(wrapper, dtype, token_ceiling):
    talker = wrapper.model.talker
    config = wrapper.model.config.talker_config
    predictor = talker.code_predictor
    device = "cuda:0"
    if not 64 <= token_ceiling <= 4096:
        raise InferenceError("invalid_token_ceiling")
    cache_tokens = max(CACHE_TOKENS, token_ceiling + 512)
    return FasterQwen3TTS(
        wrapper,
        PredictorGraph(predictor, predictor.model.config, config.hidden_size, device, dtype),
        CheckedTalkerGraph(talker.model, config, device, dtype, max_seq_len=cache_tokens),
        device=device, dtype=dtype, max_seq_len=cache_tokens,
    )


@torch.inference_mode()
def audio_chunks(streamer, text, voice, style, budget):
    steps = 0
    for samples, rate, timing in streamer.generate_custom_voice_streaming(
        text=text, language="Chinese", speaker=voice, instruct=style or None,
        max_new_tokens=budget, chunk_size=CHUNK_STEPS,
    ):
        steps = timing["total_steps_so_far"]
        if rate != 24000 or samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
            raise InferenceError("invalid_waveform")
        yield samples
    # In 0.2.6 the only exits are EOS, the token budget and a full static KV
    # cache. is_final only describes a partial chunk and can be false at EOS.
    graph = streamer.talker_graph
    if not 0 < steps < min(budget, graph.max_seq_len - graph.prompt_tokens):
        raise InferenceError("missing_eos_or_token_cap")
