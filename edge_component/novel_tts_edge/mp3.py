"""MP3 frame validation and streaming decode to 24 kHz mono 16-bit PCM WAV."""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from typing import Callable

import miniaudio

TARGET_RATE = 24000
TARGET_CHANNELS = 1
TARGET_WIDTH = 2

_SAMPLE_RATES = {
    3: (44100, 48000, 32000),  # MPEG 1
    2: (22050, 24000, 16000),  # MPEG 2
    0: (11025, 12000, 8000),   # MPEG 2.5
}

_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_BITRATES_V1_L2 = (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0)
_BITRATES_V1_L1 = (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0)
_BITRATES_V2_L1 = (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0)


class MP3ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _frame_length(data: bytes, pos: int) -> int:
    if pos + 4 > len(data):
        return -1
    b0, b1, b2 = data[pos], data[pos + 1], data[pos + 2]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return -1
    version = (b1 >> 3) & 0x03
    layer = (b1 >> 1) & 0x03
    bitrate_idx = (b2 >> 4) & 0x0F
    sr_idx = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    if version == 1 or layer == 0 or bitrate_idx in (0, 15) or sr_idx == 3:
        return -1
    sample_rate = _SAMPLE_RATES[version][sr_idx]
    if version == 3:
        table = {1: _BITRATES_V1_L3, 2: _BITRATES_V1_L2, 3: _BITRATES_V1_L1}[layer]
    else:
        table = _BITRATES_V2_L1 if layer == 3 else _BITRATES_V2_L3
    bitrate = table[bitrate_idx]
    if bitrate <= 0:
        return -1
    if layer == 3:
        return ((12000 * bitrate) // sample_rate + padding) * 4
    factor = 144000 if (version == 3 or layer == 2) else 72000
    return (factor * bitrate) // sample_rate + padding


def validate_mp3_frames(data: bytes) -> int:
    """Validate that data contains complete MPEG audio frames without truncation."""
    if not data:
        raise MP3ValidationError("empty_audio", "未收到有效音频数据")
    start = 0
    end = len(data)
    # Skip optional ID3v2 header.
    if end >= 10 and data[:3] == b"ID3":
        flags = data[5]
        size_bytes = data[6:10]
        if any(b & 0x80 for b in size_bytes):
            raise MP3ValidationError("decode_error", "MP3 ID3v2 标签无效")
        tag_size = (
            (size_bytes[0] << 21)
            | (size_bytes[1] << 14)
            | (size_bytes[2] << 7)
            | size_bytes[3]
        )
        start = 10 + tag_size + (10 if (flags & 0x10) else 0)
        if start >= end:
            raise MP3ValidationError("truncated_audio", "下载的 MP3 音频被截断")
    # Trim optional trailing ID3v1 tag.
    if end - start >= 128 and data[end - 128 : end - 125] == b"TAG":
        end -= 128

    pos = start
    frames = 0
    while pos < end:
        length = _frame_length(data, pos)
        if length <= 0:
            if frames == 0:
                raise MP3ValidationError("decode_error", "音频数据不是有效的 MP3 格式")
            raise MP3ValidationError("truncated_audio", "下载的 MP3 音频不完整或包含损坏帧")
        if pos + length > end:
            raise MP3ValidationError("truncated_audio", "下载的 MP3 音频在帧尾被截断")
        pos += length
        frames += 1

    if frames == 0:
        raise MP3ValidationError("empty_audio", "未解析到有效 MP3 帧")
    return frames


def decode_mp3_to_wav(
    mp3_bytes: bytes,
    destination: Path,
    *,
    max_mp3_bytes: int,
    max_pcm_bytes: int,
    max_duration_seconds: int,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[int, int, int, int]:
    """Decode validated MP3 bytes into 24 kHz mono 16-bit PCM WAV."""
    if not mp3_bytes:
        raise MP3ValidationError("empty_audio", "未收到有效音频数据")
    if len(mp3_bytes) > max_mp3_bytes:
        raise MP3ValidationError("size_limit", "下载的 MP3 超过单段大小上限")
    validate_mp3_frames(mp3_bytes)

    max_frames = max_duration_seconds * TARGET_RATE
    total_frames = 0
    total_pcm_bytes = 0
    completed = False
    try:
        try:
            stream = miniaudio.stream_memory(
                mp3_bytes,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=TARGET_CHANNELS,
                sample_rate=TARGET_RATE,
                frames_to_read=8192,
            )
        except Exception as exc:
            raise MP3ValidationError("decode_error", f"MP3 解码初始化失败（{type(exc).__name__}）") from exc

        with wave.open(str(destination), "wb") as wav:
            wav.setparams(
                (TARGET_CHANNELS, TARGET_WIDTH, TARGET_RATE, 0, "NONE", "not compressed")
            )
            try:
                for chunk in stream:
                    if cancel_check and cancel_check():
                        raise MP3ValidationError("cancelled", "已取消")
                    if not chunk:
                        continue
                    if sys.byteorder != "little":
                        chunk.byteswap()
                    raw = chunk.tobytes()
                    chunk_frames = len(raw) // (TARGET_CHANNELS * TARGET_WIDTH)
                    total_frames += chunk_frames
                    total_pcm_bytes += len(raw)
                    if total_frames > max_frames:
                        raise MP3ValidationError("duration_limit", "解码后的音频超过单段时长上限")
                    if total_pcm_bytes > max_pcm_bytes:
                        raise MP3ValidationError("size_limit", "解码后的音频超过单段大小上限")
                    wav.writeframes(raw)
            except MP3ValidationError:
                raise
            except Exception as exc:
                raise MP3ValidationError("decode_error", f"MP3 解码失败（{type(exc).__name__}）") from exc
        if total_frames <= 0:
            raise MP3ValidationError("empty_audio", "MP3 解码后没有有效音频帧")
        completed = True
        return total_frames, TARGET_RATE, TARGET_CHANNELS, TARGET_WIDTH
    finally:
        if not completed:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
