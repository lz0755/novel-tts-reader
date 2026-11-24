import json
import os
from pathlib import Path

from PySide6.QtCore import QSettings

from novel_tts.ui.local import runtime_paths
from novel_tts_sidecar.worker import read_settings


def test_moved_bundle_and_explicit_runtime_selection(tmp_path):
    prefs = QSettings(str(tmp_path / "user.ini"), QSettings.Format.IniFormat)
    desktop = tmp_path / "新位置 空格"
    root = desktop / "local-qwen"
    python = root / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    settings = root / "models/runtime.json"
    assert runtime_paths(prefs, desktop) is None
    for path in (python, settings):
        path.parent.mkdir(parents=True)
        path.touch()
    assert runtime_paths(prefs, desktop) == (python, settings)
    custom = tmp_path / "other.json"
    custom.touch()
    prefs.setValue("sidecar_python", str(python))
    prefs.setValue("sidecar_settings", str(custom))
    assert runtime_paths(prefs, desktop) == (python, custom)
    custom.unlink()
    assert runtime_paths(prefs, desktop) == (python, settings)


def test_model_paths_follow_configuration_and_legacy_absolute_paths(
    tmp_path, monkeypatch
):
    directory = tmp_path / "随包 模型"
    directory.mkdir()
    config = directory / "runtime.json"
    external = tmp_path / "external"
    config.write_text(
        json.dumps(
            {
                "models": {
                    "bundled": {"path": "0.6B-85e237c1"},
                    "external": {"path": str(external)},
                }
            }
        ),
        encoding="utf-8-sig",
    )
    monkeypatch.chdir(tmp_path)
    entries = read_settings(config)["models"]
    assert Path(entries["bundled"]["path"]) == directory / "0.6B-85e237c1"
    assert Path(entries["external"]["path"]) == external


def test_runtime_paths_also_discovers_qwen_runtime_layout(tmp_path):
    prefs = QSettings(str(tmp_path / "user.ini"), QSettings.Format.IniFormat)
    desktop = tmp_path / "便携 路径"
    root = desktop / "qwen_runtime"
    python = root / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    settings = root / "models/runtime.json"
    for path in (python, settings):
        path.parent.mkdir(parents=True)
        path.touch()
    assert runtime_paths(prefs, desktop) == (python, settings)


def test_package_qwen_accepts_desktop_release_cli_mode(tmp_path, capsys):
    import sys

    import pytest

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import package_qwen
    finally:
        sys.path.pop(0)

    empty_desktop = tmp_path / "empty-desktop"
    empty_desktop.mkdir()
    with pytest.raises(SystemExit) as exc:
        package_qwen.main(["--desktop", str(empty_desktop), "--output", str(tmp_path / "out")])
    assert exc.value.code == 2
    assert "missing native desktop executable" in capsys.readouterr().err

