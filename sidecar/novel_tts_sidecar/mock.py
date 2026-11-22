import math
import os
import struct
import time
import wave


class Runner:
    def __init__(self, settings):
        self.model = None

    def load(self, data):
        self.model = data["model"]
        return dict(
            model=self.model,
            revision="mock-1",
            voices=["mock-voice"],
            instruct=False,
            origin="mock_tone",
            streaming=True,
        )

    def synthesize(self, data, path, publish=None, cancelled=None):
        scenario = data.get("scenario")
        if scenario == "exit":
            os._exit(7)
        if scenario == "hang":
            time.sleep(300)
        if scenario == "oom":
            raise MemoryError()
        with path.open("wb", buffering=0) as raw, wave.open(raw, "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            for start in range(0, 7200, 2400):
                wav.writeframesraw(b"".join(
                    struct.pack("<h", int(2100 * math.sin(i * 0.08)))
                    for i in range(start, start + 2400)
                ))
                if publish:
                    publish(start + 2400)
                if scenario == "stream_slow":
                    time.sleep(0.2)
                if cancelled and cancelled():
                    return {}
        return {"frames": 7200, "rate": 24000, "origin": "mock_tone"}
