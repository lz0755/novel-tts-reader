from dataclasses import replace

from novel_tts.domain.models import Cancelled
from novel_tts.providers.llm import chunks
from novel_tts.providers.http import ContextLimit


def analyze_chapter(repo, view, caps, remote, event, report=lambda n: None):
    expected = view.revision
    count = 0
    for page in range(view.page_count):
        base = repo.call("request", view.chapter, page)
        repo.call("guard", view.book, expected)
        pending = [
            (chunk, False) for chunk in chunks(base, remote.profile.context_bytes)
        ]
        while pending:
            chunk, already_split = pending.pop(0)
            if event.is_set():
                raise Cancelled()
            repo.call("guard", view.book, expected)
            characters = repo.call("characters", view.book) + repo.call(
                "aliases", view.book
            )
            req = replace(chunk, revision=expected, characters=characters)
            try:
                result = remote.analyze_chunk(req, event)
            except ContextLimit:
                if already_split or len(req.targets) < 2:
                    raise
                half = len(req.targets) // 2
                first = replace(
                    req, targets=req.targets[:half], batch_id=req.batch_id + "-a"
                )
                second = replace(
                    req,
                    targets=req.targets[half:],
                    batch_id=req.batch_id + "-b",
                    context=(req.context + "".join(p.text for p in first.targets))[-256:],
                )
                pending[:0] = [(first, True), (second, True)]
                continue
            if event.is_set():
                raise Cancelled()
            repo.call("commit_annotations", req, result, caps)
            expected += 1
            count += 1
            report(count)
    return count
