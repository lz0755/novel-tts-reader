"""Run or score the fixed corpus with all missing and invalid responses in the denominator."""

import argparse
import json
from collections import Counter
from pathlib import Path
from threading import Event

from novel_tts.domain.models import AnnotationRequest, Parent
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.profiles import Profiles
from novel_tts.providers.llm import RemoteLLM

CORPUS = Path(__file__).resolve().parents[1] / "fixtures/regression.json"


def score(corpus, rows):
    index = {r["id"]: r.get("parts", []) for r in rows}
    c = Counter()
    errors = []
    for chapter in corpus["chapters"]:
        for p in chapter["parents"]:
            expected = p["gold"]
            got = index.get(p["id"], [])
            if [(g["start"], g["end"]) for g in got] != [
                (g["start"], g["end"]) for g in expected
            ]:
                c["boundary_errors"] += 1
            if p["kind"] == "multi":
                c["multi"] += 1
                c["multi_correct"] += int(
                    [(x["start"], x["end"]) for x in got]
                    == [(x["start"], x["end"]) for x in expected]
                )
            for g in expected:
                if g["speaker"] == "unknown":
                    c["ambiguous"] += 1
                if g["speaker"] not in ("narrator", "unknown"):
                    c["clear"] += 1
                    if g in got:
                        c["clear_correct"] += 1
                    else:
                        errors.append(p["id"])
            c["unreturned"] += int(not got)
    return dict(
        c,
        clear_accuracy=c["clear_correct"] / max(1, c["clear"]),
        error_ids=list(dict.fromkeys(errors)),
    )


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--score", type=Path)
    cli.add_argument("--profile", choices=["openai", "deepseek", "custom"])
    cli.add_argument(
        "--send-corpus",
        action="store_true",
        help="发送十章受控测试文本，消耗配置的请求预算",
    )
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    if args.score:
        rows = [
            json.loads(line)
            for line in args.score.read_text(encoding="utf-8").splitlines()
        ]
        print(json.dumps(score(corpus, rows), ensure_ascii=False))
        return
    if not args.send_corpus or not args.profile or not args.output:
        cli.error("请指定 --score，或同时提供 --send-corpus --profile --output")
    profiles = Profiles(Paths().data)
    p = profiles.all()[args.profile]
    rows = []
    with args.output.open("x", encoding="utf-8") as out:

        def save(row):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()

        remote = RemoteLLM(
            p,
            profiles.credential(p),
            record=lambda r: save(
                dict(
                    id="usage-" + r["attempt"],
                    kind="usage",
                    chapter=chapter["title"],
                    **r,
                )
            ),
        )
        for chapter in corpus["chapters"]:
            # Character discovery is part of the task; do not supply the gold identities.
            confirmed = {}
            for i in range(0, len(chapter["parents"]), 4):
                batch = chapter["parents"][i : i + 4]
                req = AnnotationRequest(
                    "evaluation",
                    chapter["title"],
                    "gold-v1",
                    0,
                    batch[0]["id"],
                    tuple(Parent(x["id"], 0, x["text"]) for x in batch),
                    characters=tuple(confirmed.items()),
                )
                try:
                    result = remote.analyze_chunk(req, Event())
                except Exception as exc:
                    save(dict(id=batch[0]["id"], error=str(exc)))
                    if not remote.budget.left:
                        break
                    continue
                names = dict(confirmed)
                for person in result.candidates:
                    names[person.ref] = person.name
                    confirmed["c-" + person.name] = person.name
                for ident, parts in result.lines:
                    row = dict(
                        id=ident,
                        parts=[
                            dict(
                                start=x.start,
                                end=x.end,
                                speaker=names.get(x.speaker, x.speaker),
                            )
                            for x in parts
                        ],
                    )
                    save(row)
                    rows.append(row)
            if not remote.budget.left:
                break
    print(json.dumps(score(corpus, rows), ensure_ascii=False))


if __name__ == "__main__":
    main()
