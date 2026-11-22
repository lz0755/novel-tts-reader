"""Build and exercise a relocatable, multi-volume desktop with the offline Qwen engine."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PYTHON_BUILD = "20261110"
PYTHON_VERSION = "3.12.16"
RUNTIMES = {
    "linux": (
        "unknown-linux-gnu",
        "6a9e62dad033790aef722dbd370e0ce76ce17b73627ba3ae679f537049350b39",
    ),
    "windows": (
        "pc-windows-msvc",
        "355ee61b8207b91574491b1c6dc9b0ebb4408fc56bfafe498f3ef439314b52dc",
    ),
}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare_runtime(local, temp, target):
    platform_name, checksum = RUNTIMES[target]
    filename = f"cpython-{PYTHON_VERSION}%2B{PYTHON_BUILD}-x86_64-{platform_name}-install_only_stripped.tar.gz"
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_BUILD}/{filename}"
    archive = temp / "python.tar.gz"
    subprocess.run(
        ["curl", "--fail", "--location", "--retry", "5", "-o", archive, url], check=True
    )
    if digest(archive) != checksum:
        raise ValueError("Portable Python checksum mismatch")
    with tarfile.open(archive) as package:
        package.extractall(local, filter="data")
    python = local / "python" / ("python.exe" if target == "windows" else "bin/python3")
    lock = local / "pylock.qwen.toml"
    subprocess.run(
        [
            "uv",
            "export",
            "--project",
            PROJECT / "sidecar",
            "--locked",
            "--no-dev",
            "--extra",
            "qwen",
            "--no-emit-project",
            "--format",
            "pylock.toml",
            "-o",
            lock,
        ],
        stdout=subprocess.DEVNULL,
        check=True,
    )
    # The cache lives on the same volume so the large CUDA libraries use hard links.
    cache = temp / "wheel-cache"
    options = [
        "--python",
        python,
        "--break-system-packages",
        "--cache-dir",
        cache,
        "--link-mode",
        "hardlink",
    ]
    subprocess.run(["uv", "pip", "sync", *options, lock], check=True)
    subprocess.run(
        ["uv", "pip", "install", *options, "--no-deps", PROJECT / "sidecar"], check=True
    )
    shutil.rmtree(cache)
    subprocess.run(
        [
            python,
            "-I",
            "-m",
            "novel_tts_sidecar.prepare",
            "--model",
            "0.6B",
            "--relative-paths",
            "--root",
            local / "models",
        ],
        env=dict(os.environ, HF_HUB_DISABLE_XET="1", HF_HUB_DOWNLOAD_TIMEOUT="180"),
        check=True,
    )
    for cache_dir in (local / "models").glob("*/.cache"):
        shutil.rmtree(cache_dir)
    shutil.copy2(PROJECT / "packaging/Qwen-Model-LICENSE.txt", local / "models")
    shutil.copy2(PROJECT / "sidecar/uv.lock", local / "sidecar-uv.lock")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--desktop", required=True, type=Path, help="the tested native Nuitka directory"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args()
    target = "windows" if os.name == "nt" else "linux"
    binary = "NovelTTSReader" + (".exe" if os.name == "nt" else "")
    if not (args.desktop / binary).is_file():
        parser.error("missing native desktop executable")
    seven_zip = shutil.which("7z") or shutil.which("7zz")
    if not seven_zip:
        parser.error("7-Zip is required to build the split archive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prefix = f"NovelTTSReader-Qwen-0.6B-{target}-x86_64"
    if any(output.glob(prefix + ".*")):
        parser.error("use an empty output directory for this target")
    with tempfile.TemporaryDirectory(prefix="qwen-build-", dir=args.work) as temp_name:
        temp = Path(temp_name)
        desktop = temp / "NovelTTSReader-Qwen"
        shutil.copytree(args.desktop, desktop, symlinks=True)
        local = desktop / "local-qwen"
        local.mkdir()
        prepare_runtime(local, temp, target)
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True
        ).strip()
        (local / "build.json").write_text(
            json.dumps(
                {
                    "commit": commit,
                    "python": PYTHON_VERSION,
                    "python_distribution_sha256": RUNTIMES[target][1],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        archive = output / (prefix + ".7z")
        subprocess.run(
            [
                seven_zip,
                "a",
                "-t7z",
                "-mx=1",
                "-mmt=2",
                "-ms=off",
                "-snl",
                "-v1500m",
                archive,
                desktop.name,
            ],
            cwd=temp,
            check=True,
        )
        shutil.rmtree(desktop)
        parts = sorted(output.glob(archive.name + ".[0-9][0-9][0-9]"))
        if not parts or any(p.stat().st_size >= 2**31 for p in parts):
            raise ValueError("Invalid split archive")
        moved = temp / "离线 校验"
        subprocess.run([seven_zip, "x", "-y", "-o" + str(moved), parts[0]], check=True)
        desktop = moved / desktop.name
        local = desktop / "local-qwen"
        python = local / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
        offline = dict(
            os.environ,
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            HF_HOME=str(temp / "empty-hf"),
            TORCH_HOME=str(temp / "empty-torch"),
            PYTHONNOUSERSITE="1",
            PYTHONUTF8="1",
            QT_QPA_PLATFORM="offscreen",
            OMP_NUM_THREADS="2",
        )
        subprocess.run(
            [desktop / binary, "--self-test"],
            cwd=moved,
            env=offline,
            timeout=150,
            check=True,
        )
        subprocess.run(
            [
                python,
                "-I",
                "-X",
                "utf8",
                PROJECT / "scripts/check_qwen_bundle.py",
                local,
                output / (prefix + ".check.json"),
                output / (prefix + ".sample.wav"),
            ],
            cwd=moved,
            env=offline,
            timeout=900,
            check=True,
        )
        (output / (prefix + ".sha256")).write_text(
            "".join(f"{digest(part)}  {part.name}\n" for part in parts),
            encoding="ascii",
        )


if __name__ == "__main__":
    main()
