from dataclasses import replace

from novel_tts.domain.models import Cancelled
from novel_tts.providers.llm import RemoteLLM, chunks


def analyze_chapter(repo,view,caps,remote,event,report=lambda n:None):
    expected = view.revision
    count = 0
    for page in range(view.page_count):
        base = repo.call("request",view.chapter,page)
        repo.call("guard",view.book,expected)
        for chunk in chunks(base,remote.profile.context_bytes):
            if event.is_set():
                raise Cancelled()
            repo.call("guard",view.book,expected)
            characters = repo.call("characters",view.book)+repo.call("aliases",view.book)
            req = replace(chunk,revision=expected,characters=characters)
            result = remote.analyze_chunk(req,event)
            if event.is_set():
                raise Cancelled()
            repo.call("commit_annotations",req,result,caps)
            expected += 1
            count += 1
            report(count)
    return count
