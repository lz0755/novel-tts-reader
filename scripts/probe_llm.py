"""Probe cloud annotation with a fixed public sample."""

import argparse
import json
from pathlib import Path
from threading import Event

from novel_tts.domain.models import AnnotationRequest, Parent, fingerprint
from novel_tts.domain.text import parents
from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.profiles import Profiles
from novel_tts.providers.llm import RemoteLLM


def probe_request(fixture):
    if fixture == "dialogue_multiline":
        path = Path(__file__).resolve().parents[1] / "fixtures/dialogue_multiline.txt"
        source = path.read_text(encoding="utf-8")
        return AnnotationRequest(
            "synthetic-book",
            "synthetic-chapter",
            fingerprint(source),
            0,
            "a5128746fd38430598479d1f540a3dcc-p0",
            parents(source),
            characters=(
                ("narrator", "旁白"),
                ("unknown", "待确认"),
                ("f582b46ac281447d8b71ad6d4fcbd922", "沈禾"),
            ),
        )
    return AnnotationRequest(
        "probe",
        "chapter",
        "r1",
        0,
        "probe-1",
        (Parent("s17", 0, "林渊说：“别走。”少年阿宁答：“好。”"),),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile", choices=["openai", "deepseek", "custom"], required=True
    )
    parser.add_argument(
        "--fixture",
        choices=["dialogue_short", "dialogue_multiline"],
        default="dialogue_short",
    )
    args = parser.parse_args()
    profiles = Profiles(Paths().data)
    p = profiles.all()[args.profile]
    req = probe_request(args.fixture)
    try:
        remote = RemoteLLM(
            p,
            profiles.credential(p),
            record=lambda row: print(json.dumps(row, ensure_ascii=False)),
        )
        result = remote.analyze_chunk(req, Event())
        print(
            json.dumps(
                dict(
                    verified=True,
                    fixture=args.fixture,
                    model=result.model,
                    targets=len(req.targets),
                    parts=sum(len(parts) for _, parts in result.lines),
                    candidates=len(result.candidates),
                )
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                dict(verified=False, profile=p.name, reason=str(exc)),
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
