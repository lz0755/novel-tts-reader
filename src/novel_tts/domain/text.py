from __future__ import annotations

import bisect
import codecs
import hashlib
import re
from dataclasses import dataclass

import regex

from novel_tts.domain.models import Parent

HEADING = re.compile(
    r"(?im)^[ \t　]*(?:第[零〇一二两三四五六七八九十百千万\d]+[章节卷回部]|chapter\s+\d+)[^\n]{0,75}$"
)
GRAPHEME = regex.compile(r"\X")
SENTENCE = regex.compile(
    r"[^。！？!?；;\n]+[。！？!?；;]*[”’」』]*|[。！？!?；;”’」』]+|\n+"
)


@dataclass(frozen=True)
class ImportDraft:
    title: str
    raw: bytes
    encoding: str
    source: str
    digest: str
    boundaries: tuple[int, ...]


def read_draft(raw: bytes, title: str, encoding: str = "auto") -> ImportDraft:
    if encoding == "auto":
        if raw.startswith(codecs.BOM_UTF8):
            encoding = "utf-8-sig"
        elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            try:
                raw.decode("utf-8", "strict")
                encoding = "utf-8"
            except UnicodeDecodeError:
                encoding = "gb18030"
    decoded = raw.decode(encoding, "strict")
    if not decoded.strip() or "\x00" in decoded:
        raise ValueError("文件为空或编码不匹配")
    source = decoded.replace("\r\n", "\n").replace("\r", "\n")
    boundaries = tuple(
        sorted({0, *(match.start() for match in HEADING.finditer(source))})
    )
    return ImportDraft(
        title, raw, encoding, source, hashlib.sha256(raw).hexdigest(), boundaries
    )


def decoded_offsets(draft, canonical_points):
    """Map selected canonical code points to decoded-source offsets, including CRLF."""
    decoded = draft.raw.decode(draft.encoding, "strict")
    points = sorted(set(canonical_points))
    if points and not 0 <= points[0] <= points[-1] <= len(draft.source):
        raise ValueError("原文锚点超出范围")
    mapped = {}
    cp = raw = 0
    for needle in points:
        while cp < needle:
            raw += 2 if decoded.startswith("\r\n", raw) else 1
            cp += 1
        mapped[needle] = raw
    return mapped


def chapters(draft, boundaries):
    cuts = tuple(boundaries)
    if (
        not cuts
        or cuts[0] != 0
        or list(cuts) != sorted(set(cuts))
        or cuts[-1] >= len(draft.source)
    ):
        raise ValueError("章节起点必须从 0 开始并严格递增")
    for start, end in zip(cuts, cuts[1:] + (len(draft.source),)):
        if start and draft.source[start - 1] != "\n":
            raise ValueError("章节须从原文行首开始")
        source = draft.source[start:end]
        label = source.split("\n", 1)[0].strip()[:64] or "正文"
        yield start, label, source


def parents(source: str, limit: int = 400) -> tuple[Parent, ...]:
    result = []
    position = 0
    for line in source.splitlines(keepends=True):
        while len(line) > limit:
            edges = [
                m.end()
                for m in GRAPHEME.finditer(line, endpos=min(len(line), limit + 128))
                if m.end() <= limit
            ]
            if not edges:
                size = GRAPHEME.match(line).end()
            else:
                natural = [i for i in edges if line[i - 1] in "。！？；，、 \t"]
                size = (
                    natural[-1] if natural and natural[-1] >= limit // 2 else edges[-1]
                )
            result.append(Parent(f"p{position}", position, line[:size]))
            position += size
            line = line[size:]
        if line:
            result.append(Parent(f"p{position}", position, line))
            position += len(line)
    return tuple(result)


def boundaries(text: str) -> set[int]:
    return {0, *(m.end() for m in GRAPHEME.finditer(text))}


class DisplayMap:
    """UTF-16 indices for a single visible window, with grapheme-safe selection."""

    def __init__(self, text: str, offset: int = 0):
        self.offset = offset
        self.cp = [0]
        self.qt = [0]
        for m in GRAPHEME.finditer(text):
            self.cp.append(m.end())
            self.qt.append(self.qt[-1] + len(m.group().encode("utf-16-le")) // 2)

    def to_qt(self, cp: int, ceil=False) -> int:
        return self.qt[self._index(self.cp, cp - self.offset, ceil)]

    def from_qt(self, pos: int, ceil=False) -> int:
        return self.cp[self._index(self.qt, pos, ceil)] + self.offset

    @staticmethod
    def _index(values, pos, ceil):
        index = (
            bisect.bisect_left(values, pos)
            if ceil
            else bisect.bisect_right(values, pos) - 1
        )
        return min(len(values) - 1, max(0, index))
