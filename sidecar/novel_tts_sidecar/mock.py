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
        )

    def synthesize(self, data, path):
        scenario = data.get("scenario")
        if scenario == "exit":
            os._exit(7)
        if scenario == "hang":
            time.sleep(300)
        if scenario == "oom":
            raise MemoryError()
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(
                b"".join(
                    struct.pack("<h", int(2100 * math.sin(i * 0.08)))
                    for i in range(7200)
                )
            )
        return {"frames": 7200, "rate": 24000, "origin": "mock_tone"}
