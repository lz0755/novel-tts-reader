import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

from novel_tts_sidecar import attention
from novel_tts_sidecar.qwen import Runner


def test_render_enables_inference_mode_for_voice_generation(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    runner = Runner({})
    runner.instruct = False
    runner.model = Mock()
    talker = runner.model.model.talker
    runner.model.model.config.talker_config.codec_eos_token_id = 7
    talker.generate.return_value.sequences = torch.tensor([[7]])

    def generate(**kwargs):
        assert torch.is_inference_mode_enabled()
        talker.generate()
        return [torch.ones(8).numpy()], 24000

    runner.model.generate_custom_voice.side_effect = generate
    with torch.inference_mode(False):
        wave, rate, _ = runner.render("雾散了。", "vivian", "")
        assert not torch.is_inference_mode_enabled()
    assert len(wave) == 8 and rate == 24000


@pytest.mark.parametrize("length,masked", [(4, False), (1, False), (4, True)])
def test_expanded_sdpa_matches_grouped_attention(length, masked):
    module = SimpleNamespace(num_key_value_groups=2, is_causal=True)
    rng = torch.Generator().manual_seed(17)
    q = torch.randn(1, 4, length, 8, generator=rng)
    k, v = torch.randn(2, 1, 2, 4, 8, generator=rng)
    mask = (
        torch.ones(length, 4, dtype=torch.bool).tril()[None, None] if masked else None
    )
    actual, _ = attention.expanded_kv_sdpa(module, q, k, v, mask)
    expected, _ = attention.sdpa_attention_forward(module, q, k, v, mask)
    torch.testing.assert_close(actual, expected)


@pytest.mark.parametrize(
    "backends,expected",
    [
        (["MATH", "EFFICIENT_ATTENTION"] * 2, attention.EXPANDED_SDPA),
        (["FLASH_ATTENTION"], "sdpa"),
        (["MATH", "MATH"], "sdpa"),
        (["MATH", "EFFICIENT_ATTENTION", "CUDNN_ATTENTION"], "sdpa"),
    ],
)
def test_select_sdpa_checks_prefill_and_decode(
    tmp_path, monkeypatch, backends, expected
):
    config = {"num_attention_heads": 4, "num_key_value_heads": 2, "head_dim": 8}
    (tmp_path / "config.json").write_text(json.dumps({"talker_config": config}))
    cpu_empty = torch.empty
    monkeypatch.setattr(
        torch, "empty", lambda shape, *, device, dtype: cpu_empty(shape, dtype=dtype)
    )
    choices = iter(backends)
    calls = []

    def choose(q, k, v, *, is_causal, enable_gqa):
        calls.append((q.shape[2], k.shape[1], is_causal, enable_gqa))
        return getattr(attention.SDPBackend, next(choices)).value

    monkeypatch.setattr(torch, "_fused_sdp_choice", choose)
    assert attention.select_attention("sdpa", "bfloat16", tmp_path) == expected
    assert calls == [
        (32, 2, True, True),
        (32, 4, True, True),
        (1, 2, False, True),
        (1, 4, False, True),
    ][: len(backends)]
    if expected == attention.EXPANDED_SDPA:
        masks = attention.AttentionMaskInterface()
        assert masks[expected] is masks["sdpa"]
        assert attention.AttentionInterface()[expected] is attention.expanded_kv_sdpa


@pytest.mark.parametrize(
    "backend,dtype",
    [("eager", "bfloat16"), ("flash_attention_2", "float16"), ("sdpa", "float32")],
)
def test_other_attention_options_bypass_probe(tmp_path, backend, dtype):
    assert attention.select_attention(backend, dtype, tmp_path) == backend
