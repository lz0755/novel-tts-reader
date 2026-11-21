import random
from dataclasses import replace

import pytest

from novel_tts.domain.annotations import MockLLM, validate
from novel_tts.domain.models import Candidate, Part, Stale
from novel_tts.domain.text import DisplayMap, boundaries, chapters, read_draft
from novel_tts.infrastructure.store import page_chunks


def test_encodings_mapping():
    text = "第一章 渡口\r\n𠮷👩‍🚀e\u0301。\r\n第一章 渡口\n终"
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        draft = read_draft(text.encode(encoding), "示例")
        assert len(list(chapters(draft, draft.boundaries))) == 2
        assert draft.source == text.replace("\r\n", "\n")
        assert draft.raw.decode(draft.encoding) == text
    with pytest.raises(ValueError):
        read_draft(b"", "")
    with pytest.raises(UnicodeDecodeError):
        read_draft(b"\x80", "")
    value = draft.source
    m = DisplayMap(value, 500)
    for cp in boundaries(value):
        qt = len(value[:cp].encode("utf-16-le")) // 2
        assert m.to_qt(500 + cp) == qt and m.from_qt(qt) == 500 + cp
    interior = value.index("👩") + 1
    assert m.to_qt(500 + interior) != m.to_qt(500 + interior, True)


def test_million_char_window_and_20_random_anchors():
    text = "长路有灯。𠮷👩‍🚀e\u0301\r\n" * 85000
    draft = read_draft(text.encode(), "长书")
    windows = list(page_chunks(draft.source))
    assert max(len(s) for _, s in windows) <= 8000
    assert "".join(s for _, s in windows) == draft.source
    for cp in random.Random(41).sample(range(len(draft.source)), 20):
        offset, page = next((i, p) for i, p in windows if i <= cp < i + len(p))
        assert page[cp - offset] == draft.source[cp]


def test_manual_priority_stale_and_undo(book):
    store, lib, tts, v = book
    req = store.request(v.chapter, 0)
    result = MockLLM().analyze_chunk(req)
    line = v.lines[0]
    store.save_edit(v, line.start, line.start + 2, "unknown", "轻声")
    with pytest.raises(Stale):
        store.commit_annotations(req, result, tts.capabilities())
    req = store.request(v.chapter, 0)
    store.commit_annotations(req, MockLLM().analyze_chunk(req), tts.capabilities())
    v = store.view(v.chapter, 0, tts.capabilities())
    assert v.lines[0].speaker == "unknown" and v.lines[0].display == line.display[:2]
    store.undo_edit(v)
    v = store.view(v.chapter, 0, tts.capabilities())
    assert v.lines[0].display == line.display


def test_rules_locality(book):
    store, lib, tts, v = book
    line = v.lines[0]
    before = [tts.prepare(l.spoken, l.voice, l.style).key for l in v.lines]
    for scope, rep in [("全局", "甲"), ("单书", "乙"), ("单句", "丙")]:
        store.save_rule(v, line, scope, "灯渡", rep)
        v = store.view(v.chapter, 0, tts.capabilities())
        assert v.lines[0].spoken.endswith(rep) and v.lines[0].display == line.display
    after = [tts.prepare(l.spoken, l.voice, l.style).key for l in v.lines]
    assert before[1:] == after[1:] and before[0] != after[0]


def test_invalid_annotation_and_independent_people(book):
    store, lib, tts, v = book
    req = store.request(v.chapter, 0)
    good = MockLLM().analyze_chunk(req)
    p = req.targets[0]
    bad = [
        replace(good, batch_id="wrong"),
        replace(good, lines=good.lines[1:]),
        replace(good, lines=good.lines + good.lines[:1]),
        replace(
            good, lines=((p.id, (Part(1, len(p.text), "narrator"),)),) + good.lines[1:]
        ),
        replace(
            good,
            lines=((p.id, (Part(0, len(p.text), "other-book-id"),)),) + good.lines[1:],
        ),
    ]
    for result in bad:
        with pytest.raises(ValueError):
            validate(req, result)
    candidates = tuple(Candidate(f"new{i}", "同名", p.id, 0, 1) for i in range(20))
    store.commit_annotations(
        req, replace(good, candidates=candidates), tts.capabilities()
    )
    ids = [cid for cid, name in store.characters(v.book) if name == "同名"]
    assert len(set(ids)) == 20
    assert all(
        store.binding(v.book, cid, tts.capabilities())[0] in tts.capabilities().voices
        for cid in ids
    )
