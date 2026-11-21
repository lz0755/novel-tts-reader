import math

MODELS = {
    "0.6B": (
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "85e237c12c027371202489a0ec509ded67b5e4b5",
    ),
    "1.7B": (
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "0c0e3051f131929182e2c023b9537f8b1c68adfe",
    ),
}


class InferenceError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def token_budget(text, settings):
    # Initial calibration. Spoken numbers / abbreviations consume additional time.
    units = sum(
        1.45 if ch.isascii() and ch.isalnum() else 0.35 if ch.isspace() else 1
        for ch in text
    )
    seconds = units / max(0.5, settings.get("characters_per_second", 3.0))
    return max(48, math.ceil(seconds * 12 * settings.get("length_margin", 1.55) + 30))


def safe_sections(text, settings):
    import regex

    maximum = settings.get("token_ceiling", 1536)
    if not 64 <= maximum <= 4096:
        raise InferenceError("invalid_token_ceiling")
    remaining = text
    while remaining:
        if token_budget(remaining, settings) <= maximum:
            yield remaining
            return
        candidates = []
        natural = []
        for cluster in regex.finditer(r"\X", remaining):
            end = cluster.end()
            if token_budget(remaining[:end], settings) > maximum:
                break
            candidates.append(end)
            if cluster.group() in "，；。！？、 \n":
                natural.append(end)
        if not candidates:
            raise InferenceError("unsplittable_grapheme")
        split = (
            natural[-1]
            if natural and natural[-1] >= candidates[-1] // 2
            else candidates[-1]
        )
        yield remaining[:split]
        remaining = remaining[split:]
