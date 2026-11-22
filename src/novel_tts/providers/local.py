import json
import time
import uuid
from functools import partial
from pathlib import Path
from threading import Lock

import httpx
import jsonschema

from novel_tts.domain.models import (
    Cancelled,
    Capabilities,
    ProviderError,
    SynthesisRequest,
    fingerprint,
)
from novel_tts.infrastructure.audio import inspect_wav

MODELS = (
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
)


def client_from_hello(line, token):
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts/handshake.json").read_text(
            encoding="utf-8"
        )
    )
    try:
        hello = json.loads(line)
        jsonschema.validate(hello, schema)
    except (ValueError, jsonschema.ValidationError):
        raise ProviderError("本地运行时握手或协议版本无效") from None
    return ControlClient(hello["port"], token)


class ControlClient:
    def __init__(self, port, token):
        self.base = f"http://127.0.0.1:{port}/v1/"
        self.headers = {"Authorization": "Bearer " + token}

    def connection(self):
        return httpx.Client(base_url=self.base, headers=self.headers, trust_env=False, timeout=4)

    @staticmethod
    def request(client, method, path, data=None):
        try:
            response = client.request(method, path, json=data)
            if response.is_success:
                return response
            if response.status_code in (400, 409):
                try:
                    reason = response.json()["error"]
                except (KeyError, TypeError, ValueError):
                    reason = None
                match response.status_code, reason:
                    case 409, "busy":
                        raise ProviderError("local_worker_busy", True)
                    case 409, "stream_session_changed":
                        raise ProviderError("本地模型已切换，请重新播放当前句")
                    case 409, "model_not_ready":
                        raise ProviderError("本地模型尚未就绪，请在「朗读来源」重新加载所选模型")
                    case 400, "invalid_voice_or_text":
                        raise ProviderError("本地朗读文本为空白，或所选音色不属于当前模型")
            raise ProviderError(f"本地控制服务 HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            raise ProviderError(f"无法连接本地控制服务（{type(exc).__name__}）") from exc

    def call(self, method, path, data=None):
        with self.connection() as client:
            return self.request(client, method, path, data)

    def job(self, path, body, event, timeout=310, on_pcm=None):
        with self.connection() as client:
            return self._run_job(partial(self.request, client), path, body, event, timeout, on_pcm)

    @staticmethod
    def _run_job(call, path, body, event, timeout, on_pcm):
        ident = uuid.uuid4().hex
        if event.is_set():
            raise Cancelled()
        deadline = time.monotonic() + timeout
        while True:
            try:
                call("POST", path, body | {"job_id": ident})
                break
            except ProviderError as exc:
                if exc.code != "local_worker_busy" or time.monotonic() >= deadline:
                    raise
                if event.wait(0.15):
                    raise Cancelled()
        frames = 0
        while time.monotonic() < deadline:
            if event.wait(0.04 if on_pcm else 0.12):
                try:
                    call("POST", f"jobs/{ident}/cancel", {"hard": False})
                except ProviderError:
                    pass  # The job can finish between the last poll and cancellation.
                raise Cancelled()
            row = call("GET", f"jobs/{ident}").json()
            if on_pcm and row["state"] in ("running", "succeeded"):
                while frames < row.get("pcm_frames", 0) and not event.is_set():
                    packet = call("GET", f"jobs/{ident}/pcm/{frames}").content
                    if not packet or len(packet) % 2:
                        raise ProviderError("本地流式音频块无效")
                    on_pcm(packet)
                    frames += len(packet) // 2
            if row["state"] == "succeeded":
                if event.is_set():
                    raise Cancelled()
                if on_pcm and frames != row["result"]["frames"]:
                    raise ProviderError("本地流式音频不完整")
                return ident, row["result"]
            if row["state"] not in ("running", "cancel_requested"):
                raise ProviderError(
                    "本地任务中断 · "
                    + str(
                        row.get("error")
                        or row.get("result", {}).get("error")
                        or row["state"]
                    )
                )
        call("POST", f"jobs/{ident}/cancel", {"hard": True})
        raise ProviderError("本地进程已超时并回收")

    def load(self, model, event, quantization=None):
        body = {"model": model}
        if quantization is not None:
            body["quantization"] = quantization
        _, cap = self.job("models/load", body, event)
        if cap["model"] != model or not cap["voices"] or not cap.get("revision"):
            raise ProviderError("本地模型能力与选择不匹配")
        if quantization is not None and cap.get("quantization") != quantization:
            raise ProviderError("本地运行时加载的权重量化与选择不匹配")
        return cap


class LocalTTS:
    def __init__(self, info, client=None):
        self.slot = Lock()
        self.info = info
        self.client = client
        self.origin = info["origin"]
        if self.origin not in ("local", "mock_tone"):
            raise ProviderError("本地控制服务返回了未知来源")
        self.provider = "local_qwen" if self.origin == "local" else "mock_worker"

    def capabilities(self):
        d = self.info
        return Capabilities(
            self.provider,
            d["model"],
            "loopback-v1",
            d["revision"],
            tuple(d["voices"]),
            bool(d["instruct"]),
            cancellation="soft_or_hard",
        )

    def prepare(self, text, voice, style=""):
        caps = self.capabilities()
        params = {
            k: self.info[k]
            for k in ("dtype", "attention", "tf32", "benchmark", "settings")
            if k in self.info
        }
        if self.info.get("quantization") == "int4":
            params["settings"] = {**(params.get("settings") or {}), "quantization": "int4"}
        return SynthesisRequest(
            text,
            caps.provider,
            caps.model,
            caps.revision,
            voice,
            self.origin,
            style if caps.instruct else "",
            parameters=(("runtime", fingerprint(params)),),
        )

    @property
    def can_stream(self):
        return bool(self.client and self.info.get("stream_session"))

    def synthesize(self, req, destination, cancel, on_pcm=None):
        if not self.client:
            raise ProviderError("缓存缺失，请先加载本地模型")
        body = dict(model=req.model, text=req.text, voice=req.voice)
        if req.style:
            body["style"] = req.style
        client = self.client
        if on_pcm:
            if not self.can_stream:
                raise ProviderError("请重新加载支持流式朗读的本地模型")
            body["stream_session"] = self.info["stream_session"]
        while not self.slot.acquire(timeout=0.08):
            if cancel.is_set():
                raise Cancelled()
        try:
            if on_pcm:
                ident, result = client.job("synthesis/jobs", body, cancel, on_pcm=on_pcm)
            else:
                ident, result = client.job("synthesis/jobs", body, cancel)
        finally:
            self.slot.release()
        if result.get("origin") != req.origin:
            raise ProviderError("音频来源不匹配")
        if result.get("quantization", "none") != self.info.get("quantization", "none"):
            raise ProviderError("本地权重量化已切换，请按当前模型重新生成")
        if result.get("backend", "qwen-tts") != self.info.get("backend", "qwen-tts"):
            raise ProviderError("本地推理引擎已切换，请按当前模型重新生成")
        if cancel.is_set():
            raise Cancelled()
        destination.write_bytes(client.call("GET", f"artifacts/{ident}").content)
        artifact = inspect_wav(destination, req)
        if (artifact.rate, artifact.frames) != (result["rate"], result["frames"]):
            raise ProviderError("本地音频元数据不匹配")
        return artifact
