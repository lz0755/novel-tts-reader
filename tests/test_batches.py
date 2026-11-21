from threading import Event

import pytest

from novel_tts.application.batch import AudioBatch, playable
from novel_tts.domain.models import ProviderError
from novel_tts.domain.text import read_draft
from novel_tts.providers.mock import MockTTS


def test_exact_sample_frames_for_optional_short_batch(book):
    store, lib, tts, initial = book
    raw = "“好。”“走。”“等。”"
    draft = read_draft(raw.encode(), "短句")
    b = store.add_book(draft, draft.boundaries, Event())
    chapter = store.toc(b)[0][0]
    v = store.view(chapter, 0, tts.capabilities())
    character = store.save_edit(v, 0, 4, "unknown", "", "同人")
    v = store.view(chapter, 0, tts.capabilities())
    store.save_edit(v, 4, 8, character, "")
    v = store.view(chapter, 0, tts.capabilities())
    assert v.lines[2].speaker == "unknown"
    # No boundary estimates or paid group synthesis when the next file is missing.
    assert not isinstance(playable(lib, v, 0, tts, Event(), True), AudioBatch)
    lib.chapter(v, tts, Event())
    batch = playable(lib, v, 0, tts, Event(), True)
    try:
        assert isinstance(batch, AudioBatch) and len(batch.members) == 2
        size = batch.members[0].audio.frames
        assert batch.members[1].first_frame == size and batch.frames == sum(
            m.audio.frames for m in batch.members
        )
        assert batch.member_at((size - 1) * 1000 / 24000).index == 0
        assert batch.member_at((size + 1) * 1000 / 24000).index == 1
    finally:
        batch.temporary.cleanup()


def test_first_failure_can_retry_without_placeholder(book):
    store, lib, _, view = book
    source = MockTTS("fail_once")
    with pytest.raises(ProviderError, match="首次生成失败"):
        lib.audio(view, view.lines[0], source, Event())
    assert not store.entries()
    assert lib.audio(view, view.lines[0], source, Event()).frames > 0
