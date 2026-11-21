"""Exercise the exact loopback protocol used by the desktop. Requires a prepared runtime."""

import argparse
import json
import queue
import secrets
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from novel_tts.providers.local import MODELS, client_from_hello

PROFILES = {"qwen_0_6b": MODELS[0], "qwen_1_7b_customvoice": MODELS[1]}
SAMPLES = {
    "narration_short": "晚钟敲响，桥上的灯亮了。",
    "dialogue_long": "别着急，等最后一班渡船靠岸，我们再一起走过这座桥。",
}


@contextmanager
def session(runtime, settings):
    token = secrets.token_urlsafe(32)
    proc = subprocess.Popen(
        [
            str(runtime),
            "-m",
            "novel_tts_sidecar",
            "--engine",
            "qwen",
            "--settings",
            str(settings),
            "--timeout",
            "300",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        proc.stdin.write((token + "\n").encode())
        proc.stdin.flush()
        result = queue.Queue()
        threading.Thread(
            target=lambda: result.put(proc.stdout.readline()), daemon=True
        ).start()
        client = client_from_hello(result.get(timeout=20), token)
        yield client
    finally:
        proc.stdin.close()
        try:
            proc.wait(8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def arguments(cli):
    cli.add_argument("--runtime", required=True, type=Path, help="独立运行时的 Python")
    cli.add_argument(
        "--settings", required=True, type=Path, help="准备模型得到的 runtime.json"
    )
    cli.add_argument("--profile", choices=list(PROFILES), required=True)


def main():
    cli = argparse.ArgumentParser()
    arguments(cli)
    cli.add_argument("--fixture", choices=list(SAMPLES), default="narration_short")
    cli.add_argument("--output", required=True, type=Path)
    args = cli.parse_args()
    if args.output.exists():
        cli.error("输出已存在")
    with session(args.runtime, args.settings) as client:
        event = threading.Event()
        cap = client.load(PROFILES[args.profile], event)
        print(json.dumps(cap, ensure_ascii=False))
        body = dict(
            model=cap["model"], text=SAMPLES[args.fixture], voice=cap["voices"][0]
        )
        if cap["instruct"] and args.fixture == "dialogue_long":
            body["style"] = "低声而坚定"
        job, metrics = client.job("synthesis/jobs", body, event)
        data = client.call("GET", f"artifacts/{job}").content
        with args.output.open("xb") as out:
            out.write(data)
        print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()
