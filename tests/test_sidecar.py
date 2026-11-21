import json
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest


def test_worker_supervision_and_recovery():
    root = Path(__file__).parents[1] / "sidecar"
    token = secrets.token_urlsafe(32)
    proc = subprocess.Popen(
        [sys.executable, "-m", "novel_tts_sidecar", "--timeout", "2.5"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        proc.stdin.write(token + "\n")
        proc.stdin.flush()
        hello = json.loads(proc.stdout.readline())
        assert (
            hello["version"] == 1 and hello["host"] == "127.0.0.1" and hello["port"] > 0
        )
        with httpx.Client(
            base_url=f"http://127.0.0.1:{hello['port']}/v1/",
            trust_env=False,
            headers={"Authorization": "Bearer " + token},
            timeout=4,
        ) as client:
            assert (
                client.get("health", headers={"Authorization": "wrong"}).status_code
                == 401
            )
            assert client.post("models/load", json={"extra": 1}).status_code == 400

            def submit(kind, **body):
                ident = uuid.uuid4().hex
                data = dict(job_id=ident, model="mock", **body)
                path = "models/load" if kind == "load" else "synthesis/jobs"
                assert client.post(path, json=data).status_code == 202
                return ident

            def wait(ident):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    result = client.get("jobs/" + ident).json()
                    if result["state"] not in ("running", "cancel_requested"):
                        return result["state"]
                    time.sleep(0.04)
                pytest.fail("job did not finish")

            for scenario in ("hard", "hang", "exit", "oom", "normal"):
                assert wait(submit("load")) == "succeeded"
                ident = submit(
                    "synthesis",
                    text="渡口",
                    voice="mock-voice",
                    scenario="hang" if scenario == "hard" else scenario,
                )
                if scenario == "hard":
                    assert client.get("health").json()["busy"]
                    assert (
                        client.post(
                            "jobs/" + ident + "/cancel", json={"hard": True}
                        ).status_code
                        == 200
                    )
                state = wait(ident)
                if scenario == "normal":
                    assert state == "succeeded" and client.get(
                        "artifacts/" + ident
                    ).content.startswith(b"RIFF")
                else:
                    assert state in ("interrupted", "failed")
                    assert client.get("artifacts/" + ident).status_code == 404
                    assert client.get("health").json()["pid"] is None
            client.post("shutdown", json={})
        assert proc.wait(6) == 0
    finally:
        proc.stdin.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
