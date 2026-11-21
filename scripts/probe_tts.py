import argparse
import json
from pathlib import Path
from threading import Event

from novel_tts.infrastructure.paths import Paths
from novel_tts.infrastructure.profiles import Profiles
from novel_tts.providers.cloud_tts import QwenCloudTTS


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--profile", choices=["qwen_cloud"], default="qwen_cloud")
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--fixture", choices=["dialogue_short"], default="dialogue_short")
    args = cli.parse_args()
    profiles = Profiles(Paths().data)
    p = profiles.all()[args.profile]
    if args.output.exists():
        cli.error("输出已存在")
    try:
        provider = QwenCloudTTS(
            p,
            profiles.credential(p),
            record=lambda r: print(json.dumps(r, ensure_ascii=False)),
        )
        req = provider.prepare("桥上的灯还亮着。", p.voices[0])
        art = provider.synthesize(req, args.output, Event())
        print(
            json.dumps(
                dict(
                    origin=req.origin,
                    rate=art.rate,
                    frames=art.frames,
                    sha256=art.sha256,
                )
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps(dict(verified=False, reason=str(exc)), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
