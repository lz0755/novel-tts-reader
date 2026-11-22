import asyncio
import random
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock

import httpx

from novel_tts.application.costs import pricing_key
from novel_tts.domain.models import Cancelled, ProviderError, fingerprint


class RequestBudget:
    def __init__(self, limit):
        self.left = limit
        self.lock = Lock()

    def reserve(self):
        with self.lock:
            if not self.left:
                raise ProviderError("已达到本次操作的请求预算")
            self.left -= 1


def retry_delay(value, attempt):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(
                0,
                (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds(),
            )
        except (ValueError, TypeError, OverflowError):
            return min(3, 0.3 * 2**attempt + random.uniform(0, 0.1))


class UnsupportedFormat(ProviderError):
    pass


class ContextLimit(ProviderError):
    pass


class Attempts:
    def __init__(self, logical_id=None, *, read_timeout=30):
        self.job = uuid.uuid4().hex
        self.logical_id = logical_id
        self.count = 0
        self.read_timeout = read_timeout
        self.deadline = time.monotonic() + max(90, read_timeout)


async def _cancellable_post(client, url, cancel, deadline, **kwargs):
    request = asyncio.create_task(client.post(url, **kwargs))
    try:
        while not request.done():
            if cancel.is_set():
                raise Cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise httpx.ReadTimeout("Response deadline reached")
            await asyncio.wait((request,), timeout=min(0.2, remaining))
        return request.result()
    finally:
        if not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)


class Remote:
    def __init__(self, profile, secret, budget=None, record=None, transport=None):
        self.profile = profile.validate()
        self.secret = secret
        self.budget = budget or RequestBudget(profile.budget)
        self.record = record or (lambda event: None)
        self.transport = transport

    @contextmanager
    def _post_sender(self, headers, cancel, deadline):
        options = dict(transport=self.transport, follow_redirects=False, headers=headers)
        try:
            active_loop = asyncio.get_running_loop()
        except RuntimeError:
            active_loop = None
        if (
            self.profile.protocol == "qwen_audio_http"
            or active_loop is not None
            or (
                self.transport is not None
                and not isinstance(self.transport, httpx.AsyncBaseTransport)
            )
        ):
            with httpx.Client(**options) as client:
                yield client.post
            return

        with asyncio.Runner() as runner:
            client = httpx.AsyncClient(**options)

            def send(url, **kwargs):
                return runner.run(_cancellable_post(client, url, cancel, deadline, **kwargs))

            try:
                yield send
            finally:
                runner.run(client.aclose())

    def post(self, url, body, cancel, group=None):
        group = group or Attempts()
        job = group.job
        request_hash = fingerprint(body)
        deadline = group.deadline
        headers = {"Authorization": "Bearer " + self.secret} if self.secret else {}
        with self._post_sender(headers, cancel, deadline) as send:
            while group.count < 2:
                if cancel.is_set():
                    raise Cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError("请求超过截止时间")
                self.budget.reserve()
                attempt = group.count
                group.count += 1
                row = dict(
                    job=job,
                    attempt=uuid.uuid4().hex,
                    logical_id=group.logical_id
                    or fingerprint([url, self.profile.protocol, request_hash]),
                    number=attempt + 1,
                    hash=request_hash,
                    provider=self.profile.name,
                    protocol=self.profile.protocol,
                    model=self.profile.model,
                    requested_model=self.profile.model,
                    endpoint=self.profile.endpoint,
                    usage={},
                    cost=None,
                    price_date=self.profile.price_date,
                    pricing_key=pricing_key(self.profile),
                    currency=self.profile.currency,
                    unit=self.profile.unit,
                    state="sent",
                    remote_id=None,
                )
                mode = body.get("response_format") or body.get("text", {}).get(
                    "format", {}
                )
                row["effective_format"] = (
                    mode.get("type", "text")
                    if self.profile.protocol != "qwen_audio_http"
                    else "wav"
                )
                row["requested_format"] = (
                    self.profile.output
                    if self.profile.protocol != "qwen_audio_http"
                    else "wav"
                )
                timeout = httpx.Timeout(
                    min(30, remaining),
                    connect=min(8, remaining),
                    read=min(group.read_timeout, remaining),
                )
                row["read_timeout_s"] = round(timeout.read, 3)
                self.record(row.copy())
                started = time.monotonic()
                try:
                    response = send(url, json=body, timeout=timeout)
                except Cancelled:
                    row.update(
                        state="unknown",
                        transport_error="cancelled_after_send",
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                    )
                    self.record(row)
                    raise
                except (
                    httpx.ReadError,
                    httpx.ReadTimeout,
                    httpx.WriteError,
                    httpx.WriteTimeout,
                    httpx.RemoteProtocolError,
                ) as exc:
                    elapsed = time.monotonic() - started
                    row.update(
                        state="unknown",
                        transport_error=type(exc).__name__,
                        elapsed_ms=round(elapsed * 1000),
                    )
                    self.record(row)
                    if isinstance(exc, httpx.ReadTimeout):
                        reason = f"等待响应超时（{elapsed:.1f} 秒）"
                    elif isinstance(exc, httpx.WriteTimeout):
                        reason = "发送请求超时"
                    elif isinstance(exc, httpx.WriteError):
                        reason = "发送请求时传输中断"
                    else:
                        reason = "接收响应时连接中断"
                    raise ProviderError(
                        f"{reason}，供应商可能已执行并计费", False, "unknown"
                    ) from None
                except httpx.ProxyError:
                    row["state"] = "proxy_failed"
                    self.record(row)
                    raise ProviderError(
                        "代理连接或认证失败，请检查 HTTPS 代理配置", True
                    ) from None
                except httpx.ConnectError as exc:
                    if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                        row["state"] = "certificate_failed"
                        self.record(row)
                        raise ProviderError(
                            "TLS 证书校验失败，请核对域名、系统时间及可信 CA"
                        ) from None
                    row["state"] = "connection_failed"
                    self.record(row)
                    raise ProviderError(
                        "连接失败，请检查端点、网络和代理配置", True
                    ) from None
                except httpx.RequestError:
                    row["state"] = "connection_failed"
                    self.record(row)
                    raise ProviderError(
                        "连接失败，请检查端点、网络、代理和 TLS 证书", True
                    ) from None
                row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                row["remote_id"] = response.headers.get("x-request-id")
                if response.is_success:
                    row["state"] = "received"
                    try:
                        payload = response.json()
                    except (ValueError, UnicodeError):
                        payload = {}
                    if isinstance(payload, dict):
                        row["usage"] = payload.get("usage") or {}
                        row["model"] = payload.get("model") or self.profile.model
                        row["remote_id"] = (
                            row["remote_id"]
                            or payload.get("request_id")
                            or payload.get("id")
                        )
                        row["cost"] = self.estimate(row["usage"])
                    self.record(row)
                    if cancel.is_set():
                        raise Cancelled()
                    return response
                status = response.status_code
                retryable = status == 429 or 500 <= status < 600
                row["state"] = f"http_{status}"
                self.record(row)
                if status in (400, 422):
                    try:
                        problem = response.json().get("error", {})
                    except (ValueError, AttributeError):
                        problem = {}
                    if isinstance(problem, dict):
                        if problem.get("code") in (
                            "context_length_exceeded",
                            "context_window_exceeded",
                        ):
                            raise ContextLimit("超出供应商上下文限制")
                        if problem.get("code") in (
                            "unsupported_parameter",
                            "unsupported_value",
                        ) and str(problem.get("param", "")).startswith(
                            ("response_format", "text.format")
                        ):
                            raise UnsupportedFormat("供应商明确拒绝所选结构化输出能力")
                delay = retry_delay(response.headers.get("Retry-After"), attempt)
                if retryable and not attempt and delay < deadline - time.monotonic():
                    if cancel.wait(delay):
                        raise Cancelled()
                    continue
                raise ProviderError(f"HTTP {status}", retryable)
        raise ProviderError("请求超过截止时间")

    def estimate(self, usage):
        p = self.profile
        if (
            not isinstance(usage, dict)
            or not p.price_date
            or not p.currency
            or p.input_per_million is None
        ):
            return None
        if p.unit == "characters":
            return (
                usage["characters"] * p.input_per_million / 1e6
                if isinstance(usage.get("characters"), int)
                else None
            )
        incoming = usage.get("input_tokens", usage.get("prompt_tokens"))
        outgoing = usage.get("output_tokens", usage.get("completion_tokens"))
        if (
            p.output_per_million is None
            or not isinstance(incoming, int)
            or not isinstance(outgoing, int)
        ):
            return None
        return (incoming * p.input_per_million + outgoing * p.output_per_million) / 1e6
