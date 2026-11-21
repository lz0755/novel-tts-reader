from dataclasses import replace
from threading import Event

import pytest

from novel_tts.application.analysis import analyze_chapter
from novel_tts.domain.annotations import MockLLM
from novel_tts.domain.models import Candidate, ProviderError
from novel_tts.infrastructure.profiles import DEFAULTS
from novel_tts.infrastructure.store import Store
from novel_tts.providers.http import ContextLimit
from conftest import InlineRepository


def test_twenty_bindings_survive_retry_new_chapter_and_restart(book):
    store, _, tts, v = book
    req = store.request(v.chapter, 0)
    clean = MockLLM().analyze_chunk(req)
    p = req.targets[0]
    people = tuple(Candidate(f"new-{i}", f"角色{i}", p.id, 0, 1) for i in range(20))
    result = replace(clean, candidates=people)
    store.commit_annotations(req, result, tts.capabilities())
    initial = {
        cid: store.binding(v.book, cid, tts.capabilities())
        for cid, name in store.characters(v.book)
        if name.startswith("角色")
    }
    store.commit_annotations(
        replace(req, revision=store.revision(v.book)), result, tts.capabilities()
    )
    store.view(store.toc(v.book)[1][0], 0, tts.capabilities())
    reopened = Store(store.paths)
    try:
        saved = {
            cid: reopened.binding(v.book, cid, tts.capabilities())
            for cid, name in reopened.characters(v.book)
            if name.startswith("角色")
        }
        assert len(initial) == 20 and saved == initial
    finally:
        reopened.close()


def test_chunk_split_and_rejection_preserve_source(book):
    store, _, tts, view = book
    initial = store.request(view.chapter, 0)

    class SplitProvider:
        profile = replace(DEFAULTS["custom"], context_bytes=64000)

        def analyze_chunk(self, req, event):
            if len(req.targets) == len(initial.targets):
                raise ContextLimit("模型输出被截断")
            return MockLLM().analyze_chunk(req, event)

    assert (
        analyze_chapter(
            InlineRepository(store), view, tts.capabilities(), SplitProvider(), Event()
        )
        == 2
    )
    v = store.view(view.chapter, 0, tts.capabilities())

    class Refusal(SplitProvider):
        def analyze_chunk(self, req, event):
            raise ProviderError("拒答")

    with pytest.raises(ProviderError):
        analyze_chapter(
            InlineRepository(store), v, tts.capabilities(), Refusal(), Event()
        )
    assert v == store.view(v.chapter, 0, tts.capabilities())
