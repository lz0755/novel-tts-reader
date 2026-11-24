"""Offline tests for portable Qwen runtime discovery, relative model paths, and scripts/package_qwen.py."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from novel_tts.ui.local import bundled_qwen_runtime
from novel_tts_sidecar.worker import load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_verified_model(models_root: Path, key: str = "0.6B") -> Path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from package_qwen import QWEN_MODELS, verify_file_digest
    finally:
        sys.path.pop(0)

    repo, revision = QWEN_MODELS[key]
    folder = models_root / f"{key}-{revision[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    weights = folder / "model.safetensors"
    weights.write_bytes(b"synthetic-qwen-weights-payload")
    config = folder / "config.json"
    config.write_text('{"model_type": "qwen3_tts"}\n', encoding="utf-8")
    license_file = folder / "LICENSE"
    license_file.write_text("Apache License 2.0\n", encoding="utf-8")

    manifest = {
        "repo": repo,
        "revision": revision,
        "files": {
            "model.safetensors": {
                "algorithm": "sha256",
                "digest": verify_file_digest(weights, "sha256"),
            },
            "config.json": {
                "algorithm": "git_blob_sha1",
                "digest": verify_file_digest(config, "git_blob_sha1"),
            },
            "LICENSE": {
                "algorithm": "git_blob_sha1",
                "digest": verify_file_digest(license_file, "git_blob_sha1"),
            },
        },
    }
    (folder / "novel-verified.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return folder


def test_sidecar_load_settings_resolves_relative_and_keeps_absolute_paths(tmp_path):
    base = tmp_path / "中文 路径" / "models"
    rel_model = base / "0.6B-85e237c1"
    abs_model = tmp_path / "absolute-model"
    rel_model.mkdir(parents=True)
    abs_model.mkdir(parents=True)
    cfg_path = base / "runtime.json"
    cfg_path.write_text(
        json.dumps(
            {
                "models": {
                    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice": {
                        "path": "0.6B-85e237c1",
                        "revision": "85e237c12c027371202489a0ec509ded67b5e4b5",
                    },
                    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice": {
                        "path": str(abs_model),
                        "revision": "0c0e3051f131929182e2c023b9537f8b1c68adfe",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_settings(cfg_path)
    assert (
        Path(loaded["models"]["Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"]["path"])
        == rel_model.resolve()
    )
    assert (
        Path(loaded["models"]["Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"]["path"])
        == abs_model
    )


def test_bundled_qwen_runtime_discovery(tmp_path, monkeypatch):
    bundle = tmp_path / "中文 发行包"
    qwen_dir = bundle / "qwen_runtime"
    models_dir = qwen_dir / "models"
    models_dir.mkdir(parents=True)
    settings = models_dir / "runtime.json"
    settings.write_text('{"models": {}}\n', encoding="utf-8")
    py_exe = (
        qwen_dir / "python/python.exe"
        if os.name == "nt"
        else qwen_dir / "python/bin/python3"
    )
    py_exe.parent.mkdir(parents=True)
    py_exe.write_bytes(b"python")

    # Direct discovery via sibling of executable.
    fake_exe = bundle / ("NovelTTSReader.exe" if os.name == "nt" else "NovelTTSReader")
    fake_exe.write_bytes(b"bin")
    monkeypatch.delenv("NOVEL_TTS_QWEN_RUNTIME", raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    found = bundled_qwen_runtime()
    assert found == (py_exe.resolve(), settings.resolve())

    # Portable layout (executable inside python/ subdir).
    portable_exe = bundle / "python" / ("python.exe" if os.name == "nt" else "python3")
    portable_exe.parent.mkdir(parents=True, exist_ok=True)
    portable_exe.write_bytes(b"py")
    monkeypatch.setattr(sys, "executable", str(portable_exe))
    assert bundled_qwen_runtime() == (py_exe.resolve(), settings.resolve())

    # Environment variable override takes precedence.
    override_dir = tmp_path / "custom_qwen"
    (override_dir / "models").mkdir(parents=True)
    override_cfg = override_dir / "models/runtime.json"
    override_cfg.write_text("{}\n", encoding="utf-8")
    override_py = (
        override_dir / "python/python.exe"
        if os.name == "nt"
        else override_dir / "python/bin/python3"
    )
    override_py.parent.mkdir(parents=True)
    override_py.write_bytes(b"py2")
    monkeypatch.setenv("NOVEL_TTS_QWEN_RUNTIME", str(override_dir))
    assert bundled_qwen_runtime() == (override_py.resolve(), override_cfg.resolve())


def test_package_qwen_rejects_tampered_model_weights(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from package_qwen import QWEN_MODELS, verify_model_folder
    finally:
        sys.path.pop(0)

    folder = _make_verified_model(tmp_path / "models", "0.6B")
    repo, rev = QWEN_MODELS["0.6B"]
    assert len(verify_model_folder(folder, repo, rev)["files"]) == 3

    (folder / "model.safetensors").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="校验失败"):
        verify_model_folder(folder, repo, rev)


def test_package_qwen_end_to_end_portable_bundle(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from build_desktop import stage_edge_component
        import package_qwen
    finally:
        sys.path.pop(0)

    target_platform = "windows" if os.name == "nt" else "linux"
    binary_name = "NovelTTSReader.exe" if target_platform == "windows" else "NovelTTSReader"

    core_dir = tmp_path / "core.dist"
    core_dir.mkdir()
    (core_dir / binary_name).write_bytes(b"stub-binary")
    stage_edge_component(REPO_ROOT, core_dir, target_platform)

    models_root = tmp_path / "qwen-models"
    _make_verified_model(models_root, "0.6B")

    fake_site = tmp_path / "fake-site-packages"
    dist_info = fake_site / "qwen_tts-0.1.1.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: qwen-tts\nVersion: 0.1.1\nLicense: Apache-2.0\n",
        encoding="utf-8",
    )
    (dist_info / "LICENSE").write_text("Apache-2.0 text\n", encoding="utf-8")

    out_dir = tmp_path / "dist"
    rc = package_qwen.main(
        [
            "--platform",
            target_platform,
            "--core-dir",
            str(core_dir),
            "--models-root",
            str(models_root),
            "--models",
            "0.6B",
            "--output",
            str(out_dir),
            "--sidecar-site-packages",
            str(fake_site),
            "--skip-qwen-import-check",
            "--skip-desktop-self-test",
        ]
    )
    assert rc == 0

    ext = ".zip" if target_platform == "windows" else ".tar.gz"
    archive = out_dir / f"NovelTTSReader-{target_platform}-x86_64-qwen{ext}"
    sha_file = Path(str(archive) + ".sha256")
    assert archive.is_file() and sha_file.is_file()
    expected_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert sha_file.read_text(encoding="utf-8").strip() == f"{expected_sha}  {archive.name}"

    extract_dir = tmp_path / "解压 验证 目录"
    extract_dir.mkdir()
    if target_platform == "windows":
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(extract_dir)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(extract_dir, filter="tar")

    runtime_json = extract_dir / "qwen_runtime/models/runtime.json"
    cfg = json.loads(runtime_json.read_text(encoding="utf-8"))
    assert cfg["models"]["Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"]["path"] == "0.6B-85e237c1"
    assert (extract_dir / "qwen_runtime/licenses/qwen_tts-0.1.1.dist-info/LICENSE").is_file()
    assert (extract_dir / "qwen_runtime/licenses/models/0.6B/LICENSE").is_file()
    assert (extract_dir / "qwen_runtime/qwen-uv.lock").is_file()
    assert (extract_dir / "QWEN-BUNDLE.md").is_file()

    # Verify bundled sidecar still handshakes and resolves models from the relocated Chinese+space dir.
    info = package_qwen.verify_staged_qwen_runtime(
        extract_dir / "qwen_runtime",
        target_platform,
        ["0.6B"],
        verify_imports=False,
    )
    assert info["handshake"]["version"] == 1
    assert Path(
        info["resolved"]["Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"]
    ).is_dir()
