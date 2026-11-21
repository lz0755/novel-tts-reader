"""Exercise actual paged desktop reads, 20 deterministic random jumps and restart."""

import json
import random
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from novel_tts.domain.text import read_draft
from novel_tts.infrastructure.paths import Paths
from novel_tts.ui.window import ReaderWindow


def main():
    app = QApplication([])

    def wait(predicate):
        until = time.monotonic() + 70
        while not predicate():
            if time.monotonic() > until:
                raise TimeoutError("桌面用例超时")
            app.processEvents()
            time.sleep(0.004)

    text = "渡口的灯亮了。𠮷说：“好。”👩‍🚀和e\u0301随后走上石桥。\r\n" * 40000
    draft = read_draft(text.encode(), "百万字无章标题样书")
    anchors = random.Random(42).sample(range(len(draft.source)), 20)
    with tempfile.TemporaryDirectory() as folder:
        paths = Paths(Path(folder))
        window = ReaderWindow(paths)
        try:
            window.show()
            window.add_draft(draft)
            wait(lambda: window.view is not None)
            rows = []
            for cp in anchors:
                window.load_page(cp=cp)
                wait(lambda: window.view is not None)
                v = window.view
                assert v.source == draft.source[v.offset : v.offset + len(v.source)]
                assert v.offset <= cp < v.offset + len(v.source) <= len(draft.source)
                line = v.lines[window.index]
                selections = window.text.extraSelections()
                selected = selections[0].cursor.selectedText()
                assert (
                    selected.replace("\u2029", "\n")
                    == draft.source[line.start : line.end]
                )
                rows.append(
                    dict(
                        request=cp, page=v.page, actual=line.start, window=len(v.source)
                    )
                )
            last = window.view.lines[window.index].start
        finally:
            window.close()
        restarted = ReaderWindow(paths)
        try:
            wait(lambda: restarted.view is not None)
            assert restarted.view.lines[restarted.index].start == last
        finally:
            restarted.close()
    print(
        json.dumps(
            dict(
                source_sha256=draft.digest,
                canonical_length=len(draft.source),
                jumps=rows,
                reopened_at=last,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
