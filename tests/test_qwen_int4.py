import pytest

torch = pytest.importorskip("torch")

from novel_tts_sidecar.int4 import GROUP_SIZE, Int4Linear


@pytest.mark.skipif(
    not torch.cuda.is_available() or torch.cuda.get_device_capability()[0] < 8,
    reason="The packed INT4 kernel requires an Ampere or newer CUDA GPU",
)
def test_int4_kernel_preserves_nibble_order_group_scales_and_bias():
    linear = torch.nn.Linear(256, 32, dtype=torch.bfloat16)
    groups = linear.in_features // GROUP_SIZE
    grid = torch.arange(linear.out_features * groups).reshape(linear.out_features, groups, 1)
    codes = torch.arange(GROUP_SIZE) % 16
    steps = (grid % 4 + 1) / 32
    centers = (grid % 7 - 3) / 8
    weights = ((codes - 8) * steps + centers).reshape(32, 256)
    weights[0, :GROUP_SIZE] = 0.25
    with torch.no_grad():
        linear.weight.copy_(weights)
        linear.bias.copy_(torch.arange(32) / 32)
    device = torch.device("cuda:0")
    quantized = Int4Linear(linear, device)
    assert quantized.packed_weight.nbytes == linear.weight.numel() // 2
    assert quantized.scale_center.shape == (groups, linear.out_features, 2)
    # Identity exposes every reconstructed weight. Transpose exercises strided inputs.
    x = torch.eye(256, device=device, dtype=torch.bfloat16).T.reshape(2, 128, 256)
    with torch.inference_mode():
        expected = linear.to(device)(x)
        torch.testing.assert_close(quantized(x), expected, rtol=0, atol=0)
        torch.testing.assert_close(quantized(x[0, 1]), expected[0, 1], rtol=0, atol=0)
