import json
import time
import uuid
from threading import Lock
from pathlib import Path

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

    def call(self, method, path, data=None):
        try:
            with httpx.Client(
                base_url=self.base, headers=self.headers, trust_env=False, timeout=4
            ) as client:
                response = client.request(method, path, json=data)
            if not response.is_success:
                if (
                    response.status_code == 409
                    and response.json().get("error") == "busy"
                ):
                    raise ProviderError("local_worker_busy", True)
                raise ProviderError(f"本地控制服务 HTTP {response.status_code}")
            return response
        except httpx.HTTPError as exc:
            raise ProviderError(f"无法连接本地控制服务（{type(exc).__name__}）") from exc

    def job(self, path, body, event, timeout=310):
        ident = uuid.uuid4().hex
        if event.is_set():
            raise Cancelled()
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.call("POST", path, body | {"job_id": ident})
                break
            except ProviderError as exc:
                if exc.code != "local_worker_busy" or time.monotonic() >= deadline:
                    raise
                if event.wait(0.15):
                    raise Cancelled()
        while time.monotonic() < deadline:
            if event.wait(0.12):
                self.call("POST", f"jobs/{ident}/cancel", {"hard": False})
                raise Cancelled()
            row = self.call("GET", f"jobs/{ident}").json()
            if row["state"] == "succeeded":
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
        self.call("POST", f"jobs/{ident}/cancel", {"hard": True})
        raise ProviderError("本地进程已超时并回收")

    def load(self, model, event):
        _, cap = self.job("models/load", {"model": model}, event)
        if cap["model"] != model or not cap["voices"] or not cap.get("revision"):
            raise ProviderError("本地模型能力与选择不匹配")
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

    def synthesize(self, req, destination, cancel):
        if not self.client:
            raise ProviderError("缓存缺失，请先加载本地模型")
        body = dict(model=req.model, text=req.text, voice=req.voice)
        if req.style:
            body["style"] = req.style
        client = self.client
        while not self.slot.acquire(timeout=0.08):
            if cancel.is_set():
                raise Cancelled()
        try:
            ident, result = client.job("synthesis/jobs", body, cancel)
        finally:
            self.slot.release()
        if result.get("origin") != req.origin:
            raise ProviderError("音频来源不匹配")
        if cancel.is_set():
            raise Cancelled()
        destination.write_bytes(client.call("GET", f"artifacts/{ident}").content)
        artifact = inspect_wav(destination, req)
        if (artifact.rate, artifact.frames) != (result["rate"], result["frames"]):
            raise ProviderError("本地音频元数据不匹配")
        return artifact
