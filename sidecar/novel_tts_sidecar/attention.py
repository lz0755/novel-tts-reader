import json

import torch
from torch.nn.attention import SDPBackend
from transformers import AttentionInterface, AttentionMaskInterface
from transformers.integrations.sdpa_attention import repeat_kv, sdpa_attention_forward


EXPANDED_SDPA = "sdpa_expanded_kv"


def expanded_kv_sdpa(module, query, key, value, attention_mask, **kwargs):
    # Transformers already expands grouped heads when a mask is present.
    groups = getattr(module, "num_key_value_groups", 1)
    if attention_mask is None and groups > 1:
        key = repeat_kv(key, groups)
        value = repeat_kv(value, groups)
    return sdpa_attention_forward(module, query, key, value, attention_mask, **kwargs)


def select_attention(requested, dtype, folder):
    if requested != "sdpa" or dtype not in ("bfloat16", "float16"):
        return requested

    # Both pinned CustomVoice models use the same head layout for their talker
    # and code predictor. Probe prefill and one-token decode once per load.
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    talker = config["talker_config"]
    heads, kv_heads = talker["num_attention_heads"], talker["num_key_value_heads"]
    choose = getattr(torch, "_fused_sdp_choice", None)
    if choose is None or heads == kv_heads:
        return requested
    try:
        query = torch.empty(
            (1, heads, 32, talker["head_dim"]),
            device="cuda:0",
            dtype=getattr(torch, dtype),
        )
        grouped_key = query[:, :kv_heads]
        for q, causal in ((query, True), (query[:, :, :1], False)):
            native = choose(
                q, grouped_key, grouped_key, is_causal=causal, enable_gqa=True
            )
            if native != SDPBackend.MATH.value:
                return requested
            expanded = choose(q, query, query, is_causal=causal, enable_gqa=True)
            if expanded != SDPBackend.EFFICIENT_ATTENTION.value:
                return requested
    except RuntimeError:
        # A build without the dispatch probe can still run ordinary SDPA.
        return requested

    AttentionInterface.register(EXPANDED_SDPA, expanded_kv_sdpa)
    AttentionMaskInterface.register(EXPANDED_SDPA, AttentionMaskInterface()["sdpa"])
    return EXPANDED_SDPA
