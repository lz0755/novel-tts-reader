"""Groupwise INT4 weights for the two pinned Qwen CustomVoice transformers."""

import torch
from torch import nn

from .models import InferenceError


GROUP_SIZE = 32
INNER_K_TILES = 8


class Int4Linear(nn.Module):
    def __init__(self, linear, device):
        super().__init__()
        self.in_features = linear.in_features
        self.out_features = linear.out_features
        if self.in_features % (16 * INNER_K_TILES) or self.out_features % 8:
            raise InferenceError("int4_unsupported_linear_shape")

        groups = linear.weight.detach().to(device=device, dtype=torch.float32)
        groups = groups.reshape(self.out_features, -1, GROUP_SIZE)
        low, high = torch.aminmax(groups, dim=-1)
        scale = ((high - low) / 15).clamp_min_(1e-8)
        codes = ((groups - low.unsqueeze(-1)) / scale.unsqueeze(-1)).round_()
        codes = codes.clamp_(0, 15).to(torch.uint8).reshape_as(linear.weight)
        pairs = (codes[:, ::2] << 4) | codes[:, 1::2]
        packed = torch.ops.aten._convert_weight_to_int4pack(pairs, INNER_K_TILES)

        # TinyGEMM reconstructs (code - 8) * scale + center per group.
        scale_center = torch.stack((scale, low + 8 * scale), dim=-1)
        self.register_buffer("packed_weight", packed)
        self.register_buffer(
            "scale_center", scale_center.transpose(0, 1).to(torch.bfloat16).contiguous()
        )
        bias = linear.bias.detach().to(device) if linear.bias is not None else None
        self.register_buffer("bias", bias)

    def forward(self, x):
        flat = x.reshape(-1, self.in_features).contiguous()
        out = torch.ops.aten._weight_int4pack_mm(
            flat, self.packed_weight, GROUP_SIZE, self.scale_center
        )
        if self.bias is not None:
            out = out + self.bias
        return out.reshape(*x.shape[:-1], self.out_features)


@torch.no_grad()
def quantize_talker(wrapper, device):
    talker = wrapper.model.talker
    count = 0
    for layers in (talker.model.layers, talker.code_predictor.model.layers):
        for name, module in layers.named_modules():
            if isinstance(module, nn.Linear):
                layers.set_submodule(name, Int4Linear(module, device))
                count += 1
    if not count:
        raise InferenceError("int4_no_linear_layers")

    # The Qwen API wraps the codec separately and caches the input device in
    # both wrappers. Pack one CPU layer at a time before moving the rest to CUDA.
    wrapper.model.to(device)
    wrapper.device = device
    tokenizer = wrapper.model.speech_tokenizer
    tokenizer.model.to(device)
    tokenizer.device = device
    torch.cuda.empty_cache()
    return count
