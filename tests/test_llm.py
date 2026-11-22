import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event

import httpx
import pytest

from conftest import InlineRepository
from novel_tts.application.analysis import analyze_chapter
from novel_tts.domain.models import (
    AnnotationRequest,
    Cancelled,
    Parent,
    ProviderError,
    fingerprint,
)
from novel_tts.domain.text import boundaries, read_draft
from novel_tts.infrastructure.profiles import DEFAULTS
from novel_tts.providers.llm import RemoteLLM, chunks


def answer(user):
    return {
        "schema_version": 1,
        "import_revision": user["import_revision"],
        "batch_id": user["batch_id"],
        "candidates": [],
        "lines": [
            {
                "span_id": p["id"],
                "parts": [
                    {
                        "start": 0,
                        "end": len(p["text"]),
                        "speaker": "narrator",
                        "style": "",
                    }
                ],
            }
            for p in user["targets"]
        ],
    }


def responses_reply(text):
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}
            ],
            "usage": {"input_tokens": 12, "output_tokens": 7},
        },
    )


def test_multiline_coverage_corrected_before_commit(book):
    store, _, tts, _ = book
    caps = tts.capabilities()
    sample = Path(__file__).parents[1] / "fixtures/dialogue_multiline.txt"
    draft = read_draft(sample.read_bytes(), "原创样章")
    book_id = store.add_book(draft, draft.boundaries, Event())
    chapter = store.toc(book_id)[0][0]
    view = store.view(chapter, 0, caps)
    calls = []

    def respond(request):
        messages = json.loads(request.content)["input"]
        user = json.loads(messages[-1]["content"])
        calls.append(user)
        result = answer(user)
        problem = user["targets"][1]
        assert problem["length"] == 44 and problem["text"].endswith("\n")
        if len(calls) == 1:
            # The actual cloud response stopped at 37 in this 44-code-point paragraph.
            result["lines"][1]["parts"][0]["end"] = 37
        else:
            assert store.revision(book_id) == view.revision
            assert problem["id"] in messages[0]["content"]
            assert "终点应为 44，返回 37" in messages[0]["content"]
        return responses_reply(json.dumps(result))

    profile = replace(DEFAULTS["openai"], budget=2)
    remote = RemoteLLM(
        profile,
        "",
        transport=httpx.MockTransport(respond),
        record=lambda r: store.record_attempt(book_id, r),
    )
    assert analyze_chapter(InlineRepository(store), view, caps, remote, Event()) == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    assert store.revision(book_id) == view.revision + 1
    for target in calls[0]["targets"]:
        source = target["text"]
        at = 0
        safe = boundaries(source)
        for start, end, fragment in target["anchors"]:
            assert start == at and start < end and end in safe
            assert fragment == source[start:end]
            at = end
        assert at == target["length"] == len(source)
        if source.startswith("程砚说："):
            assert target["speech_cues"] == [[0, 2, "程砚", 4], [9, 12, "许师傅", 14]]
    rows = sorted(store.attempts(), key=lambda r: r["number"])
    assert [r["validation"] for r in rows] == ["failed", "passed"]
    assert all(
        r["state"] == "received" and r["usage"]["output_tokens"] == 7 for r in rows
    )
    assert "终点应为 44，返回 37" in rows[0]["validation_error"]
    # Existing billing records from before anchors were added used the original targets.
    old_inputs = {k: v for k, v in calls[0].items() if k != "batch_id"}
    old_inputs["targets"] = [
        {k: t[k] for k in ("id", "text")} for t in old_inputs["targets"]
    ]
    logical_id = fingerprint(
        [profile.endpoint, profile.model, profile.protocol, chapter, old_inputs]
    )
    assert rows[0]["logical_id"] == rows[1]["logical_id"] == logical_id


@pytest.mark.parametrize("budget", [1, 2])
def test_invalid_speaker_stops_at_budget_and_preserves_annotations(book, budget):
    store, _, tts, view = book
    before = store.view(view.chapter, 0, tts.capabilities())

    def respond(request):
        messages = json.loads(request.content)["input"]
        result = answer(json.loads(messages[-1]["content"]))
        result["lines"][0]["parts"][0]["speaker"] = "untrusted-model-output"
        return responses_reply(json.dumps(result))

    rows = []
    remote = RemoteLLM(
        replace(DEFAULTS["openai"], budget=budget),
        "",
        transport=httpx.MockTransport(respond),
        record=rows.append,
    )
    with pytest.raises(ProviderError, match="p0.*speaker") as error:
        analyze_chapter(InlineRepository(store), view, tts.capabilities(), remote, Event())
    assert ("预算" in str(error.value)) == (budget == 1)
    assert len([r for r in rows if r.get("validation") == "failed"]) == budget
    assert "untrusted-model-output" not in json.dumps(rows) + str(error.value)
    assert store.view(view.chapter, 0, tts.capabilities()) == before


@pytest.mark.parametrize(
    "response, reason, attempts",
    [
        (httpx.Response(200, content=b"bad envelope"), "响应结构", 1),
        (responses_reply("{"), "标注 JSON 无效", 2),
        (responses_reply('{"schema_version":1}'), "缺少字段.*import_revision", 2),
        (
            httpx.Response(200, json={
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal"}]}],
            }),
            "模型拒绝",
            1,
        ),
    ],
)
def test_validation_distinguishes_bad_replies(response, reason, attempts):
    req = AnnotationRequest("b", "c", "rev", 0, "batch", (Parent("p0", 0, "雨。"),))
    rows = []
    api = RemoteLLM(
        DEFAULTS["openai"],
        "",
        record=rows.append,
        transport=httpx.MockTransport(lambda _: response),
    )
    with pytest.raises(ProviderError, match=reason):
        api.analyze_chunk(req, Event())
    assert len([r for r in rows if r.get("validation") == "failed"]) == attempts


def test_dense_paragraphs_keep_all_targets_and_context():
    targets = tuple(Parent(f"p{i}", i * 2, "雨\n") for i in range(90))
    req = AnnotationRequest("b", "c", "v1", 0, "batch", targets, "前" * 256)
    batches = list(chunks(req, 18000))
    assert max(len(batch.targets) for batch in batches) <= 32
    assert tuple(p for batch in batches for p in batch.targets) == targets
    assert len({batch.batch_id for batch in batches}) == len(batches)
    short = replace(req, targets=targets[:1], context="")
    assert list(chunks(short, 4096))[0].targets == short.targets
    prefix = req.context
    for batch in batches:
        assert batch.context == prefix[-256:]
        prefix += "".join(p.text for p in batch.targets)


@pytest.mark.parametrize("configured_wait,latency", [(None, 32), (150, 120)])
def test_slow_model_uses_its_read_timeout(book, configured_wait, latency):
    store, _, _, view = book
    req = store.request(view.chapter, 0)
    profile = DEFAULTS["custom"]
    if configured_wait is not None:
        profile = replace(profile, analysis_read_timeout=configured_wait)
    log = []

    def respond(request):
        limits = request.extensions["timeout"]
        assert limits["connect"] <= 8 and limits["write"] <= 30
        if limits["read"] < latency:
            raise httpx.ReadTimeout("model still generating")
        body = json.loads(request.content)
        result = answer(json.loads(body["messages"][-1]["content"]))
        choice = {"finish_reason": "stop", "message": {"content": json.dumps(result)}}
        return httpx.Response(200, json={"choices": [choice]})

    api = RemoteLLM(
        profile, "", record=log.append, transport=httpx.MockTransport(respond)
    )
    assert api.analyze_chunk(req, Event()).status == "completed"
    assert [r["state"] for r in log] == ["sent", "received"]


def test_cancel_during_model_generation_stops_waiting(book):
    store, _, _, view = book
    req = store.request(view.chapter, 0)
    started, closed, cancel = Event(), Event(), Event()
    log = []

    async def generate(_):
        started.set()
        try:
            await asyncio.sleep(5)
            raise AssertionError("request outlived cancellation")
        finally:
            closed.set()

    api = RemoteLLM(
        DEFAULTS["custom"], "", record=log.append, transport=httpx.MockTransport(generate)
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(api.analyze_chunk, req, cancel)
        try:
            assert started.wait(2)
        finally:
            cancel.set()
        with pytest.raises(Cancelled):
            future.result(timeout=2)
    assert closed.is_set()
    assert [r["state"] for r in log] == ["sent", "unknown"]
    assert log[-1]["transport_error"] == "cancelled_after_send"


@pytest.mark.parametrize(
    "protocol,mode",
    [
        ("responses", "json_schema"),
        ("chat_completions", "json_object"),
        ("chat_completions", "text"),
    ],
)
def test_loopback_protocol(book, protocol, mode):
    store, _, _, view = book
    req = store.request(view.chapter, 0)
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, body))
            messages = body.get("input") or body["messages"]
            result = json.dumps(answer(json.loads(messages[-1]["content"])))
            raw = (
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": result}],
                        }
                    ],
                }
                if protocol == "responses"
                else {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": result}}
                    ]
                }
            )
            raw.update(
                model="actual-model", usage={"input_tokens": 13, "output_tokens": 7}
            )
            data = json.dumps(raw).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            p = replace(
                DEFAULTS["custom"],
                endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                protocol=protocol,
                output=mode,
            )
            remote = RemoteLLM(
                p, "", record=lambda row: store.record_attempt(view.book, row)
            )
            assert remote.analyze_chunk(req, Event()).model == "actual-model"
            assert len(store.attempts()) == 1 and store.attempts()[0]["cost"] is None
        finally:
            server.shutdown()
            thread.join()
    path, body = received[0]
    if protocol == "responses":
        assert path.endswith("/responses") and body["text"]["format"]["strict"]
    else:
        assert path.endswith("/chat/completions") and ("response_format" in body) == (
            mode != "text"
        )


def test_error_budget_and_unknown(book):
    store, _, _, view = book
    req = store.request(view.chapter, 0)

    def test_error(handler, pattern, n, outcome):
        log = []
        remote = RemoteLLM(
            DEFAULTS["custom"],
            "",
            record=log.append,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderError, match=pattern):
            remote.analyze_chunk(req, Event())
        assert len({r["attempt"] for r in log}) == n and log[-1]["state"] == outcome
        assert log[-1]["cost"] is None

    def timeout(request):
        raise httpx.ReadTimeout("connection lost")

    test_error(timeout, "计费", 1, "unknown")
    test_error(lambda r: httpx.Response(401), "401", 1, "http_401")
    test_error(
        lambda r: httpx.Response(429, headers={"Retry-After": "0"}),
        "429",
        2,
        "http_429",
    )
    test_error(
        lambda r: httpx.Response(503, headers={"Retry-After": "0"}),
        "503",
        2,
        "http_503",
    )
    test_error(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": "{"}}]
            },
        ),
        "截断",
        1,
        "received",
    )
