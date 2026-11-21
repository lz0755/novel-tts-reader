import random
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock

import httpx

from novel_tts.domain.models import Cancelled, ProviderError, fingerprint


class RequestBudget:
    def __init__(self,limit):
        self.left = limit
        self.lock = Lock()

    def reserve(self):
        with self.lock:
            if not self.left:
                raise ProviderError("已达到本次操作的请求预算")
            self.left -= 1


def retry_delay(value,attempt):
    try:
        return max(0,float(value))
    except (ValueError,TypeError):
        try:
            return max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError,TypeError,OverflowError):
            return min(3,0.3*2**attempt+random.uniform(0,.1))


class Remote:
    def __init__(self,profile,secret,budget=None,record=None,transport=None):
        self.profile = profile.validate()
        self.secret = secret
        self.budget = budget or RequestBudget(profile.budget)
        self.record = record or (lambda event:None)
        self.transport = transport

    def post(self,url,body,cancel):
        job = uuid.uuid4().hex
        request_hash = fingerprint(body)
        deadline = time.monotonic()+90
        headers = {"Authorization":"Bearer "+self.secret} if self.secret else {}
        with httpx.Client(transport=self.transport,follow_redirects=False,headers=headers) as client:
            for attempt in range(2):
                if cancel.is_set():
                    raise Cancelled()
                self.budget.reserve()
                row = dict(job=job,attempt=uuid.uuid4().hex,number=attempt+1,hash=request_hash,
                           provider=self.profile.name,protocol=self.profile.protocol,model=self.profile.model,
                           endpoint=self.profile.endpoint,usage={},cost=None,price_date=self.profile.price_date,
                           currency=self.profile.currency,unit=self.profile.unit,state="sent",remote_id=None)
                self.record(row.copy())
                try:
                    response = client.post(url,json=body,timeout=httpx.Timeout(min(30,max(.5,deadline-time.monotonic())),connect=8))
                except (httpx.ReadError,httpx.ReadTimeout,httpx.WriteError,httpx.WriteTimeout,httpx.RemoteProtocolError):
                    row["state"]="unknown"
                    self.record(row)
                    raise ProviderError("响应丢失，供应商可能已执行并计费",False,"unknown") from None
                except httpx.RequestError:
                    row["state"]="connection_failed"
                    self.record(row)
                    raise ProviderError("连接失败，请检查端点、网络、代理和 TLS 证书",True) from None
                row["remote_id"] = response.headers.get("x-request-id")
                if response.is_success:
                    row["state"]="received"
                    try:
                        payload=response.json()
                    except (ValueError,UnicodeError):
                        payload={}
                    if isinstance(payload,dict):
                        row["usage"]=payload.get("usage") or {}
                        row["model"]=payload.get("model") or self.profile.model
                        row["remote_id"]=row["remote_id"] or payload.get("request_id") or payload.get("id")
                        row["cost"]=self.estimate(row["usage"])
                    self.record(row)
                    if cancel.is_set():
                        raise Cancelled()
                    return response
                status = response.status_code
                retryable = status==429 or 500<=status<600
                row["state"]=f"http_{status}"
                self.record(row)
                delay = retry_delay(response.headers.get("Retry-After"),attempt)
                if retryable and not attempt and delay<deadline-time.monotonic():
                    if cancel.wait(delay):
                        raise Cancelled()
                    continue
                raise ProviderError(f"HTTP {status}",retryable)
        raise ProviderError("请求超过截止时间")

    def estimate(self,usage):
        p = self.profile
        if not isinstance(usage,dict) or not p.price_date or not p.currency or p.input_per_million is None:
            return None
        if p.unit=="characters":
            return usage["characters"]*p.input_per_million/1e6 if isinstance(usage.get("characters"),int) else None
        incoming = usage.get("input_tokens",usage.get("prompt_tokens"))
        outgoing = usage.get("output_tokens",usage.get("completion_tokens"))
        if p.output_per_million is None or not isinstance(incoming,int) or not isinstance(outgoing,int):
            return None
        return (incoming*p.input_per_million+outgoing*p.output_per_million)/1e6
