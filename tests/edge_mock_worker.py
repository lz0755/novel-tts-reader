"""Offline test harness launching the real novel_tts_edge worker with a scripted fake backend."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp

from novel_tts_edge.mp3 import MP3ValidationError
from novel_tts_edge.worker import UpstreamStatusError, run_worker


def make_valid_mp3(frame_count: int = 10) -> bytes:
    """Build deterministic valid 24 kHz 48 kbps mono MPEG-2 Layer III frames."""
    header = bytes([0xFF, 0xF3, 0x64, 0xC4])
    frame = header + bytes(140)
    return frame * frame_count


class ScriptedBackend:
    def __init__(self, config_path: Path, log_path: Path):
        self.config_path = config_path
        self.log_path = log_path
        self.calls = 0

    def _load_config(self) -> dict[str, Any]:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _log(self, entry: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def fetch_mp3(
        self,
        *,
        text: str,
        voice: str,
        rate: str,
        pitch: str,
        volume: str,
        connect_timeout: float,
        receive_timeout: float,
        max_mp3_bytes: int,
        mark_sent: Any,
        mark_attempt: Any,
    ) -> bytes:
        self.calls += 1
        cfg = self._load_config()
        mode = cfg.get("mode", "ok")
        mark_sent()
        self._log(
            {
                "kind": "synthesize",
                "call": self.calls,
                "mode": mode,
                "text": text,
                "voice": voice,
                "stamp": time.time(),
            }
        )

        gate = cfg.get("gate_file")
        if gate:
            gate_path = Path(gate)
            while not gate_path.exists():
                await asyncio.sleep(0.02)

        delay = float(cfg.get("delay", 0.0))
        if delay > 0:
            await asyncio.sleep(delay)

        if mode == "stall":
            await asyncio.sleep(3600)
        if mode == "tone_fixture":
            return (Path(__file__).resolve().parents[1] / "fixtures/edge-tone.mp3").read_bytes()
        if mode == "empty":
            return b""
        if mode == "corrupt":
            return b"NOT_AN_MP3_STREAM"
        if mode == "truncated":
            return make_valid_mp3(10)[:-17]
        if mode == "duration_limit":
            # 25005 * 576 / 24000 = 600.12s > 600s
            return make_valid_mp3(25005)
        if mode == "size_limit":
            raise MP3ValidationError("size_limit", "下载的 MP3 超过单段大小上限")
        if mode == "forbidden_403":
            mark_attempt()
            raise UpstreamStatusError(403, "Forbidden", attempts_used=2)
        if mode == "rate_limit_429":
            retry_after = cfg.get("retry_after", 1.0)
            fail_count = int(cfg.get("fail_count", 99))
            if self.calls <= fail_count:
                raise UpstreamStatusError(429, "Too Many Requests", retry_after=retry_after)
        if mode == "throttle_then_internal_retry":
            if self.calls == 1:
                raise UpstreamStatusError(429, retry_after=0.1)
            mark_attempt()
            self._log({"kind": "upstream_retry"})
        if mode == "drop":
            fail_count = int(cfg.get("fail_count", 99))
            if self.calls <= fail_count:
                raise aiohttp.ClientConnectionError("simulated drop")
        if mode == "ok_by_frames":
            frames = int(cfg.get("frames", 10))
            return make_valid_mp3(frames)
        return make_valid_mp3(int(cfg.get("frames", 10)))

    async def list_voices(
        self, *, connect_timeout: float, receive_timeout: float
    ) -> list[dict[str, Any]]:
        cfg = self._load_config()
        self._log({"kind": "list_voices", "stamp": time.time()})
        if cfg.get("list_fail"):
            raise aiohttp.ClientConnectionError("simulated catalog refresh failure")
        return list(
            cfg.get(
                "voices",
                [
                    {
                        "short_name": "zh-CN-XiaoxiaoNeural",
                        "locale": "zh-CN",
                        "gender": "Female",
                        "friendly_name": "Microsoft Xiaoxiao Online (Natural)",
                    },
                    {
                        "short_name": "zh-CN-XiaoyiNeural",
                        "locale": "zh-CN",
                        "gender": "Female",
                        "friendly_name": "Microsoft Xiaoyi Online (Natural)",
                    },
                    {
                        "short_name": "zh-CN-YunxiNeural",
                        "locale": "zh-CN",
                        "gender": "Male",
                        "friendly_name": "Microsoft Yunxi Online (Natural)",
                    },
                    {
                        "short_name": "zh-CN-YunjianNeural",
                        "locale": "zh-CN",
                        "gender": "Male",
                        "friendly_name": "Microsoft Yunjian Online (Natural)",
                    },
                    {
                        "short_name": "zh-HK-HiuMaanNeural",
                        "locale": "zh-HK",
                        "gender": "Female",
                        "friendly_name": "Microsoft HiuMaan Online (Natural)",
                    },
                ],
            )
        )


def main() -> int:
    config_path = Path(sys.argv[1])
    log_path = Path(sys.argv[2])
    return run_worker(ScriptedBackend(config_path, log_path))


if __name__ == "__main__":
    raise SystemExit(main())
