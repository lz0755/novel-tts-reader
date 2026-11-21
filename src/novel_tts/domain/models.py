"""Immutable cross-thread contracts. All text offsets are Unicode code points."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

CONTRACT = 1
Origin = Literal["mock_fixture", "mock_tone", "cloud", "local"]


def fingerprint(value: object) -> str:
    packed = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Capabilities:
    provider: str
    model: str
    protocol: str
    revision: str
    voices: tuple[str, ...] = ()
    instruct: bool = False
    formats: tuple[str, ...] = ()
    input_limit: int = 600
    limit_unit: str = "codepoints"
    alignment: bool = False
    cancellation: str = "discard"
    verified_on: str = ""


@dataclass(frozen=True)
class Parent:
    id: str
    start: int
    text: str


@dataclass(frozen=True)
class AnnotationRequest:
    book: str
    chapter: str
    import_revision: str
    revision: int
    batch_id: str
    targets: tuple[Parent, ...]
    context: str = ""
    characters: tuple[tuple[str, str], ...] = ()
    prompt_version: str = "zh-1"
    schema_version: int = CONTRACT


@dataclass(frozen=True)
class Part:
    start: int
    end: int
    speaker: str
    style: str = ""


@dataclass(frozen=True)
class Candidate:
    ref: str
    name: str
    span_id: str
    start: int
    end: int


@dataclass(frozen=True)
class AnnotationResult:
    batch_id: str
    import_revision: str
    lines: tuple[tuple[str, tuple[Part, ...]], ...]
    candidates: tuple[Candidate, ...] = ()
    model: str = ""
    usage: tuple[tuple[str, int], ...] = ()
    status: str = "completed"
    schema_version: int = CONTRACT


@dataclass(frozen=True)
class SynthesisRequest:
    text: str
    provider: str
    model: str
    model_revision: str
    voice: str
    origin: Origin
    style: str = ""
    language: str = "Chinese"
    parameters: tuple[tuple[str, str], ...] = ()
    processing: str = "pcm24k-mono-s16-v1"

    @property
    def key(self) -> str:
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class Ticket:
    job_id: str
    attempt_id: str
    request_hash: str
    book: str
    revision: int


@dataclass(frozen=True)
class JobEvent:
    ticket: Ticket
    state: str
    playback_epoch: int = 0
    detail: str = ""


@dataclass(frozen=True)
class AudioArtifact:
    path: Path
    sha256: str
    frames: int
    rate: int
    channels: int
    width: int
    request: SynthesisRequest

    @property
    def seconds(self) -> float:
        return self.frames / self.rate


class ProviderError(Exception):
    def __init__(self, code: str, retryable: bool = False, outcome: str = "known"):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.outcome = outcome


class Cancelled(ProviderError):
    def __init__(self):
        super().__init__("已取消", outcome="unconfirmed")


class Stale(Exception):
    """The immutable request snapshot is no longer current."""


class Cancellation(Protocol):
    def is_set(self) -> bool: ...
    def wait(self, timeout: float) -> bool: ...
