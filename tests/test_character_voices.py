import json
from dataclasses import replace
from threading import Event

import httpx
import pytest

from novel_tts.domain.annotations import MockLLM, validate
from novel_tts.domain.text import read_draft
from novel_tts.domain.voices import dialogue_voice_types, identity_voice_type
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.profiles import DEFAULTS
from novel_tts.infrastructure.store import Store
from novel_tts.providers.llm import RemoteLLM
from novel_tts.providers.local import LocalTTS, MODELS
from novel_tts.providers.mock import MockTTS
from novel_tts.ui.window import ReaderWindow
from test_mock_flow import wait


def local(model):
    return LocalTTS({
        "model": model,
        "revision": "test",
        "origin": "local",
        "voices": ["Ryan", "Serena", "Dylan", "Vivian"],
        "instruct": model == MODELS[1],
    })


def test_analysis_casts_new_and_existing_roles_across_local_models(book):
    store, _, mock, _ = book
    draft = read_draft("林渊说：“走吧。”\n叶宁答：“好。”".encode(), "两位旅客")
    book_id = store.add_book(draft, draft.boundaries, Event())
    chapter = store.toc(book_id)[0][0]
    initial = store.view(chapter, 0, mock.capabilities())
    existing = {name: cid for cid, name in initial.characters}["林渊"]
    req = store.request(chapter, 0)

    def respond(request):
        body = json.loads(request.content)
        schema = body["text"]["format"]["schema"]
        assert "voice_hints" in schema["required"]
        obj = {
            "schema_version": 1,
            "batch_id": req.batch_id,
            "import_revision": req.import_revision,
            "candidates": [{
                "ref": "new", "name": "叶宁",
                "evidence": {"span_id": req.targets[1].id, "start": 0, "end": 2},
            }],
            "voice_hints": [
                {"speaker": existing, "voice_type": "male"},
                {"speaker": "new", "voice_type": "female"},
            ],
            "lines": [],
        }
        for target, speaker in zip(req.targets, (existing, "new")):
            start = target.text.index("“")
            obj["lines"].append(dict(span_id=target.id, parts=[
                dict(start=0, end=start, speaker="narrator", style=""),
                dict(start=start, end=len(target.text), speaker=speaker, style="轻声"),
            ]))
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(obj)}]}
        ]})

    api = RemoteLLM(DEFAULTS["openai"], "", transport=httpx.MockTransport(respond))
    result = api.analyze_chunk(req, Event())
    store.commit_annotations(req, result, mock.capabilities())
    female = {name: cid for cid, name in store.characters(book_id)}["叶宁"]
    for model in MODELS:
        provider = local(model)
        view = store.view(chapter, 0, provider.capabilities())
        parts = {line.speaker: line for line in view.lines}
        assert parts[existing].voice == "Dylan" and parts[female].voice == "Vivian"
        line = parts[female]
        job = provider.prepare(line.spoken, line.voice, line.style)
        assert job.voice == "Vivian" and job.key != replace(job, voice="Ryan").key
        assert bool(job.style) == (model == MODELS[1])

    caps = local(MODELS[0]).capabilities()
    store.set_voice(view, female, caps, "Ryan")
    store.commit_annotations(replace(req, revision=store.revision(book_id)), result, caps)
    assert store.binding(book_id, female, caps) == ("Ryan", False)
    assert store.binding(book_id, female, replace(caps, voices=("ryan",))) == ("ryan", False)
    assert store.binding(book_id, female, replace(caps, voices=("Vivian", "Dylan"))) == ("Dylan", True)
    assert store.binding(book_id, female, caps) == ("Ryan", False)
    assert store.binding(book_id, female, local(MODELS[1]).capabilities()) == ("Vivian", False)


def test_v6_bindings_corrected_from_existing_text_without_cloud(tmp_path):
    paths = Paths(tmp_path)
    store = Store(paths)
    draft = read_draft(
        "阿宁说：“我听见他说话了。”\n她轻声问：“走吗？”\n林渊说：“她也来吗？”\n世子林渊撑开了伞。".encode(),
        "桥边",
    )
    book = store.add_book(draft, draft.boundaries, Event())
    chapter = store.toc(book)[0][0]
    view = store.view(chapter, 0, MockTTS().capabilities())
    ids = {name: cid for cid, name in view.characters}
    caps = local(MODELS[0]).capabilities()
    profile = caps.provider + "/" + caps.model
    with store.db:
        # A legacy annotation linked the pronoun's dialogue to this known role.
        store.db.execute("UPDATE parts SET speaker=? WHERE chapter=? AND speaker='unknown'",
                         (ids["阿宁"], chapter))
        store.db.executemany("INSERT INTO bindings VALUES(?,?,?,?,0)",
                             [(book, ids["阿宁"], profile, "Ryan"), (book, ids["林渊"], profile, "Serena")])
        store.db.executescript("DROP TABLE voice_hints; DROP INDEX parts_by_speaker; PRAGMA user_version=6;")
    store.close()

    migrated = Store(paths)
    try:
        voices = {line.speaker: line.voice for line in migrated.view(chapter, 0, caps).lines}
        assert voices[ids["阿宁"]] == "Vivian" and voices[ids["林渊"]] == "Dylan"
        assert migrated.binding(book, ids["阿宁"], replace(caps, voices=("Ryan",))) == ("Ryan", True)
        assert migrated.binding(book, ids["阿宁"], caps) == ("Vivian", False)
        assert migrated.binding(book, "narrator", caps) == ("Ryan", False)
        assert migrated.path.with_suffix(".before-v7.sqlite3").exists()
    finally:
        migrated.close()


@pytest.mark.parametrize("bad", ["unknown", "narrator", "skip", "not-in-book", "duplicate"])
def test_casting_hints_must_refer_to_an_actual_speaking_role(book, bad):
    store, _, _, view = book
    req = store.request(view.chapter, 0)
    result = MockLLM().analyze_chunk(req)
    cid = {name: cid for cid, name in req.characters}["阿宁"]
    hints = ((cid, "female"), (cid, "male")) if bad == "duplicate" else ((bad, "female"),)
    with pytest.raises(ValueError, match="voice_hints"):
        validate(req, replace(result, voice_hints=hints))


def test_legacy_inference_distinguishes_speaker_from_people_mentioned():
    assert dialogue_voice_types("他对她说：", "", "") == set()
    assert dialogue_voice_types("她轻声说：“", "", "") == {"female"}
    assert dialogue_voice_types("", "”他说。", "") == {"male"}
    assert dialogue_voice_types("", "她说：“再见。”", "平静") == set()
    assert dialogue_voice_types("", "", "年轻女声，轻柔") == {"female"}
    assert dialogue_voice_types("", "", "模仿男声") == set()
    assert identity_voice_type("阿宁（女）") == "female"
    assert identity_voice_type("叶宁", "绰号", "的年幼儿子") == "male"


def test_legacy_titles_do_not_merge_people_with_the_same_name(book):
    store, _, mock, _ = book
    draft = read_draft("阿宁说：“等一下。”\n少女阿宁搬过椅子。".encode(), "同名角色")
    book_id = store.add_book(draft, draft.boundaries, Event())
    chapter = store.toc(book_id)[0][0]
    view = store.view(chapter, 0, mock.capabilities())
    line = next(line for line in view.lines if line.speaker != "narrator")
    second = store.save_edit(view, line.start, line.end, line.speaker, "", new_name="阿宁")
    caps = local(MODELS[0]).capabilities()
    with store.db:
        for cid in (line.speaker, second):
            store.db.execute("INSERT INTO bindings VALUES(?,?,?,?,0)",
                             (book_id, cid, caps.provider + "/" + caps.model, "Ryan"))
            assert store.binding(book_id, cid, caps) == ("Ryan", False)


def test_voice_selector_saves_the_preset_id_shown_for_the_role(app, tmp_path):
    win = ReaderWindow(Paths(tmp_path), force_mock=True)
    try:
        win.add_draft(read_draft("少女阿宁说：“晚安。”".encode(), "晚安"))
        wait(app, lambda: bool(win.view))
        win.switch_voice(local(MODELS[0]))
        wait(app, lambda: win.view and any(line.voice == "Vivian" for line in win.view.lines))
        win.seek(next(i for i, line in enumerate(win.view.lines) if line.voice == "Vivian"))
        assert "明亮女声" in win.voice.currentText() and win.voice.currentData() == "Vivian"
        win.voice.setCurrentIndex(win.voice.findData("Serena"))
        win.change_voice()
        wait(app, lambda: win.view and win.view.lines[win.index].voice == "Serena")
        assert "温柔女声" in win.voice.currentText()
    finally:
        win.close()
