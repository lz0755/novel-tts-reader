import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from novel_tts.domain.models import ProviderError, SynthesisRequest


def test_pure_core_import():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, novel_tts.ports; assert not {'torch','PySide6','qwen_tts'}.intersection(sys.modules)",
        ],
        check=True,
    )


def test_fingerprint_and_error():
    sample = SynthesisRequest("好。", "mock", "fixture", "1", "alto", "mock_tone")
    assert len(sample.key) == 64
    assert ProviderError("timeout", False, "unknown").outcome == "unknown"


def test_handshake():
    schema = json.loads(
        (
            Path(__file__).parents[1] / "src/novel_tts/contracts/handshake.json"
        ).read_text()
    )
    data = dict(version=1, host="127.0.0.1", port=37421, pid=12)
    jsonschema.validate(data, schema)
    for invalid in [
        dict(version=2),
        dict(port=0),
        dict(host="0.0.0.0"),
        dict(token="bad"),
    ]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data | invalid, schema)
