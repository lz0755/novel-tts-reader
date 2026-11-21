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


def worker(pipe, folder, engine, settings):
    threading.Thread(target=parent_watch, daemon=True).start()
    if engine == "qwen":
        from .qwen import Runner
    else:
        from .mock import Runner
    runner = Runner(settings)
    while True:
        kind, job, data = pipe.recv()
        try:
            if kind == "load":
                result = runner.load(data)
            else:
                result = runner.synthesize(data, Path(folder) / (job + ".wav"))
            pipe.send((job, "succeeded", result))
        except Exception as exc:
            # Only a short category leaves the inference process; prompts and credentials are private.
            code = getattr(exc, "code", type(exc).__name__)
            pipe.send((job, "failed", {"error": str(code)[:90]}))
