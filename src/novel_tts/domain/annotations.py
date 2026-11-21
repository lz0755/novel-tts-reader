import re

from novel_tts.domain.models import (
    AnnotationRequest,
    AnnotationResult,
    Candidate,
    Capabilities,
    Part,
)
from novel_tts.domain.text import SENTENCE, boundaries

SPECIAL = {"narrator", "unknown", "skip"}
QUOTE = re.compile(r"[“「‘][^”」’\n]*[”」’]")
KNOWN = ("林渊", "阿宁")


def validate(request: AnnotationRequest, result: AnnotationResult):
    if (
        result.schema_version,
        result.batch_id,
        result.import_revision,
        result.status,
    ) != (1, request.batch_id, request.import_revision, "completed"):
        raise ValueError("标注版本、请求或完成状态不匹配")
    targets = {p.id: p for p in request.targets}
    if [line[0] for line in result.lines] != list(targets) or len(targets) != len(
        request.targets
    ):
        raise ValueError("目标父段重复、缺失或顺序错误")
    allowed = SPECIAL | {key for key, _ in request.characters}
    for c in result.candidates:
        p = targets.get(c.span_id)
        if not c.name.strip() or not c.ref or c.ref in allowed or not p:
            raise ValueError("新角色引用无效")
        safe = boundaries(p.text)
        if c.start not in safe or c.end not in safe or c.start >= c.end:
            raise ValueError("新角色证据无效")
        allowed.add(c.ref)
    for span_id, parts in result.lines:
        p = targets[span_id]
        safe = boundaries(p.text)
        offset = 0
        for part in parts:
            if (
                part.start != offset
                or part.end <= offset
                or part.end not in safe
                or part.speaker not in allowed
            ):
                raise ValueError("原文覆盖、字形边界或角色引用无效")
            if part.speaker == "skip" and re.search(
                r"[\w\u3400-\u9fff]", p.text[part.start : part.end]
            ):
                raise ValueError("正文不可标为装饰")
            offset = part.end
        if offset != len(p.text):
            raise ValueError("原文覆盖不完整")
    return result


def narration(text, offset=0):
    safe = boundaries(text)
    at = 0
    for m in SENTENCE.finditer(text):
        if m.end() not in safe:
            continue
        value = text[at : m.end()]
        kind = "narrator" if re.search(r"[\w\u3400-\u9fff]", value) else "skip"
        yield Part(offset + at, offset + m.end(), kind)
        at = m.end()


class RuleNarration:
    def analyze_chunk(self, request, cancel=None):
        lines = tuple((p.id, tuple(narration(p.text))) for p in request.targets)
        return validate(
            request,
            AnnotationResult(
                request.batch_id, request.import_revision, lines, model="rule-narrator"
            ),
        )


class MockLLM:
    def capabilities(self):
        return Capabilities("mock", "fixture", "offline", "1", formats=("spans",))

    def analyze_chunk(self, request, cancel=None):
        from novel_tts.domain.models import Cancelled

        refs = {name: key for key, name in request.characters}
        candidates, lines = [], []
        for p in request.targets:
            if cancel and cancel.is_set():
                raise Cancelled()
            parts = []
            at = 0
            safe = boundaries(p.text)
            for quote in QUOTE.finditer(p.text):
                if quote.start() not in safe or quote.end() not in safe:
                    continue
                lead = p.text[at : quote.start()]
                parts.extend(narration(lead, at))
                last = max(
                    ((lead.rfind(name), name) for name in KNOWN), default=(-1, "")
                )
                speaker = "unknown"
                if last[0] >= 0:
                    name = last[1]
                    if name not in refs:
                        ref = f"new-{len(candidates)}"
                        refs[name] = ref
                        place = at + last[0]
                        candidates.append(
                            Candidate(ref, name, p.id, place, place + len(name))
                        )
                    speaker = refs[name]
                parts.append(Part(quote.start(), quote.end(), speaker))
                at = quote.end()
            parts.extend(narration(p.text[at:], at))
            lines.append((p.id, tuple(parts)))
        return validate(
            request,
            AnnotationResult(
                request.batch_id,
                request.import_revision,
                tuple(lines),
                tuple(candidates),
                "mock",
            ),
        )
