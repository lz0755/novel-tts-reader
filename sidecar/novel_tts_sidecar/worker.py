import json
import multiprocessing as mp
import os
import threading
import time
from pathlib import Path


def parent_watch():
    parent = mp.parent_process()
    while parent and parent.is_alive():
        time.sleep(0.25)
    os._exit(2)


def load_settings(settings):
    if not isinstance(settings, Path):
        return settings or {}
    path = settings.resolve()
    options = json.loads(path.read_text(encoding="utf-8-sig"))
    base = path.parent
    models = options.get("models")
    if isinstance(models, dict):
        for entry in models.values():
            if isinstance(entry, dict) and entry.get("path"):
                model_path = Path(entry["path"])
                if not model_path.is_absolute():
                    entry["path"] = str((base / model_path).resolve())
    return options


def worker(pipe, folder, engine, settings, cancel_event=None):
    threading.Thread(target=parent_watch, daemon=True).start()
    if engine == "qwen":
        from .qwen import Runner
    else:
        from .mock import Runner
    while True:
        kind, job, data = pipe.recv()
        try:
            if kind == "load":
                # Each new worker reads the selected file, including on a reload
                # of the same model. Older control processes pass a snapshot.
                options = load_settings(settings)
                runner = Runner(options)
                result = runner.load(data)
            else:
                def publish(frames):
                    pipe.send((job, "pcm", frames))

                result = runner.synthesize(
                    data, Path(folder) / (job + ".wav"),
                    publish if "stream_session" in data else None,
                    cancel_event.is_set if cancel_event is not None else None,
                )
            pipe.send((job, "succeeded", result))
        except Exception as exc:
            # Only a short category leaves the inference process; prompts and credentials are private.
            code = getattr(exc, "code", type(exc).__name__)
            pipe.send((job, "failed", {"error": str(code)[:90]}))
