from pathlib import Path
from typing import Protocol

from novel_tts.domain.models import (
    AnnotationRequest, AnnotationResult, AudioArtifact, Cancellation,
    Capabilities, SynthesisRequest,
)


class LLMProvider(Protocol):
    def capabilities(self) -> Capabilities: ...
    def analyze_chunk(self, request: AnnotationRequest, cancel: Cancellation) -> AnnotationResult: ...


class TTSProvider(Protocol):
    def capabilities(self) -> Capabilities: ...
    def prepare(self, text: str, voice: str, style: str) -> SynthesisRequest: ...
    def synthesize(self, request: SynthesisRequest, destination: Path, cancel: Cancellation) -> AudioArtifact: ...
