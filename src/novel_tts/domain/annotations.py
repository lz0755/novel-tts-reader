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
    if result.schema_version != 1 or result.status != "completed":
        raise ValueError("标注版本或完成状态不匹配")
    if result.batch_id != request.batch_id:
        raise ValueError("批次标识 batch_id 与请求不一致")
    if result.import_revision != request.import_revision:
        raise ValueError("原文修订 import_revision 与请求不一致")
    targets = {p.id: p for p in request.targets}
    if len(targets) != len(request.targets):
        raise ValueError("请求的原文段 ID 重复")
    if len(result.lines) != len(targets):
        raise ValueError(f"原文段数不符，应有 {len(targets)} 段，返回 {len(result.lines)} 段")
    for index, (target, line) in enumerate(zip(request.targets, result.lines), 1):
        if target.id != line[0]:
            raise ValueError(f"第 {index} 段的 span_id 应为 {target.id}")
    allowed = SPECIAL | {key for key, _ in request.characters}
    for index, c in enumerate(result.candidates, 1):
        p = targets.get(c.span_id)
        if not c.name.strip() or not c.ref.strip() or c.ref in allowed:
            raise ValueError(f"第 {index} 个候选角色的名字或引用为空，或引用已被占用")
        if p is None:
            raise ValueError(f"第 {index} 个候选角色的证据 span_id 不属于本批原文")
        safe = boundaries(p.text)
        if c.start not in safe or c.end not in safe or c.start >= c.end:
            raise ValueError(
                f"第 {index} 个候选角色在 {p.id} 的证据区间 [{c.start}, {c.end}) 无效，"
                f"应在 0 到 {len(p.text)} 之间且位于完整字形边界"
            )
        allowed.add(c.ref)
    for span_id, parts in result.lines:
        p = targets[span_id]
        safe = boundaries(p.text)
        offset = 0
        for index, part in enumerate(parts, 1):
            where = f"原文段 {span_id} 第 {index} 个区间"
            if part.start != offset:
                raise ValueError(f"{where} 起点应为 {offset}，返回 {part.start}")
            if not part.start < part.end <= len(p.text):
                raise ValueError(f"{where} [{part.start}, {part.end}) 无效，段长为 {len(p.text)}")
            if part.end not in safe:
                raise ValueError(f"{where} 的终点 {part.end} 拆开了一个字形")
            if part.speaker not in allowed:
                raise ValueError(f"{where} 的 speaker 未引用已知角色或本批候选角色")
            if part.speaker == "skip" and re.search(
                r"[\w\u3400-\u9fff]", p.text[part.start : part.end]
            ):
                raise ValueError(f"{where} 含有正文，不可标为 skip")
            offset = part.end
        if offset != len(p.text):
            raise ValueError(f"原文段 {span_id} 覆盖不完整，终点应为 {len(p.text)}，返回 {offset}")
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
