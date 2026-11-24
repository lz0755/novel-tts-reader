import asyncio
import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from novel_tts_edge.mp3 import MP3ValidationError, decode_mp3_to_wav
from novel_tts_edge.worker import RealEdgeBackend, WorkerSession, _classify_error


class EdgeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, packets, *, unfinished_bytes=0, limit=6):
        async def stream():
            for packet in packets:
                yield packet

        with patch("novel_tts_edge.worker.edge_tts.Communicate") as communicate:
            communicate.return_value.stream = stream
            communicate.return_value.state = {"chunk_audio_bytes": unfinished_bytes}
            result = await RealEdgeBackend().fetch_mp3(
                text="测试。", voice="zh-CN-XiaoxiaoNeural", rate="+0%", pitch="+0Hz",
                volume="+0%", connect_timeout=3, receive_timeout=7, max_mp3_bytes=limit,
                mark_sent=Mock(), mark_attempt=Mock(),
            )
            communicate.assert_called_once_with(
                text="测试。", voice="zh-CN-XiaoxiaoNeural", rate="+0%", pitch="+0Hz",
                volume="+0%", connect_timeout=3, receive_timeout=7,
            )
            return result

    async def test_collects_audio_in_order_ignoring_metadata(self):
        result = await self.fetch([
            {"type": "audio", "data": b"abc"},
            {"type": "SentenceBoundary", "text": "测试。"},
            {"type": "audio", "data": b"def"},
        ])
        self.assertEqual(result, b"abcdef")

    async def test_incomplete_turn_empty_response_and_download_limit(self):
        audio = {"type": "audio", "data": b"abc"}
        for packets, pending, limit, code in [
            ([audio], 3, 6, "truncated_audio"),
            ([{"type": "SentenceBoundary"}], 0, 6, "empty_audio"),
            ([audio, audio], 0, 5, "size_limit"),
        ]:
            with self.subTest(code=code), self.assertRaises(MP3ValidationError) as error:
                await self.fetch(packets, unfinished_bytes=pending, limit=limit)
            self.assertEqual(error.exception.code, code)


class EdgeResponseTests(unittest.TestCase):
    def test_http_status_and_retry_after_from_client_headers(self):
        with patch("novel_tts_edge.worker.datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for status, header, retryable, delay in [
                (403, "30", False, None),
                (429, "3", True, 3),
                (429, "Thu, 01 Jan 2026 00:00:09 GMT", True, 9),
                (429, "invalid", True, None),
            ]:
                with self.subTest(status=status, header=header):
                    exc = aiohttp.ClientResponseError(
                        None, (), status=status, headers={"Retry-After": header}
                    )
                    result = _classify_error(exc, sent=True, attempts_used=1)
                    self.assertEqual(result["http_status"], status)
                    self.assertEqual(result["retryable"], retryable)
                    self.assertEqual(result.get("retry_after"), delay)

    def test_pcm_size_limit_removes_partial_wave(self):
        mp3 = (Path(__file__).resolve().parents[2] / "fixtures/edge-tone.mp3").read_bytes()
        with tempfile.TemporaryDirectory() as scratch:
            dest = Path(scratch) / "tone.partial"
            with self.assertRaises(MP3ValidationError) as error:
                decode_mp3_to_wav(
                    mp3, dest, max_mp3_bytes=len(mp3), max_pcm_bytes=5760 * 2 - 1,
                    max_duration_seconds=600,
                )
            self.assertEqual(error.exception.code, "size_limit")
            self.assertFalse(dest.exists())


class WorkerCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.destination = Path(temporary.name) / "语音.partial"
        self.output = io.StringIO()

        async def fetch_mp3(**options):
            options["mark_sent"]()
            return b"mp3"

        self.backend = Mock(fetch_mp3=AsyncMock(side_effect=fetch_mp3))
        self.worker = WorkerSession(self.backend, self.output)

    def start_job(self):
        self.worker.handle_command({
            "cmd": "synthesize", "job_id": "first", "voice": "zh-CN-XiaoxiaoNeural",
            "text": "测试。", "destination": str(self.destination),
        })
        return self.worker._tasks["first"][0]

    def cancel_job(self):
        self.worker.handle_command({"cmd": "cancel", "job_id": "first"})

    async def test_cancel_before_first_packet_dispatch_acknowledges_without_sending(self):
        task = self.start_job()
        self.cancel_job()
        await asyncio.gather(task, return_exceptions=True)
        self.backend.fetch_mp3.assert_not_awaited()
        reply = json.loads(self.output.getvalue())
        self.assertEqual(reply["error_code"], "cancelled")
        self.assertFalse(reply["sent"])
        self.assertIsNone(self.worker._synth_active)

    async def test_cancel_during_decode_waits_for_file_to_close(self):
        entered, release = threading.Event(), threading.Event()

        def decode(mp3, destination, **options):
            with destination.open("wb") as file:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release decoder")
                file.write(b"late audio")
            return 1, 24000, 1, 2

        with patch("novel_tts_edge.worker.decode_mp3_to_wav", decode):
            task = self.start_job()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.cancel_job()
                self.cancel_job()
                done, _ = await asyncio.wait([task], timeout=0.05)
                self.assertFalse(done, "worker acknowledged cancellation with an open file")
            finally:
                release.set()
                await task
        reply = json.loads(self.output.getvalue())
        self.assertEqual(reply["error_code"], "cancelled")
        self.assertTrue(reply["sent"])
        self.assertFalse(self.destination.exists())
