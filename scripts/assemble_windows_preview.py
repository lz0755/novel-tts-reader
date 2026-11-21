"""Assemble a portable Windows preview from verified native wheels and CPython.
Native Nuitka release builds run separately in the Windows workflow.
"""

import argparse
import hashlib
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

RUNTIME = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%2B20261110-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
DIGEST = "355ee61b8207b91574491b1c6dc9b0ebb4408fc56bfafe498f3ef439314b52dc"


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--out", required=True, type=Path)
    cli.add_argument(
        "--launcher", type=Path, help="由随附 C 源码编译的 Windows GUI 入口"
    )
    cli.add_argument("--stage", required=True, type=Path)
    args = cli.parse_args()
    project = Path(__file__).resolve().parents[1]
    args.stage.mkdir(exist_ok=True, parents=True)
    bundle = args.stage / "NovelTTSReader"
    archive = args.stage / "cpython.tar.gz"
    bundle.mkdir(exist_ok=True)
    if not archive.is_file():
        urllib.request.urlretrieve(RUNTIME, archive)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != DIGEST:
            raise ValueError("Windows Python 运行时摘要不匹配")
    with tarfile.open(archive) as runtime:
        runtime.extractall(bundle, filter="data")
    python = bundle / "python"
    packages = python / "Lib/site-packages"
    requirements = args.stage / "windows-lock.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-annotate",
            "--output-file",
            str(requirements),
        ],
        cwd=project,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "x86_64-pc-windows-msvc",
            "--python-version",
            "3.12",
            "--only-binary",
            ":all:",
            "--require-hashes",
            "--target",
            str(packages),
            "-r",
            str(requirements),
        ],
        check=True,
    )
    shutil.copytree(
        project / "src/novel_tts",
        packages / "novel_tts",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    if args.launcher:
        shutil.copy2(args.launcher, bundle / "NovelTTSReader.exe")
    (bundle / "NovelTTSReader.cmd").write_bytes(
        b'@echo off\r\nstart "" "%~dp0python\\pythonw.exe" -X utf8 -I -m novel_tts %*\r\n'
    )
    entry = "NovelTTSReader.exe" if args.launcher else "NovelTTSReader.cmd"
    (bundle / "README.txt").write_text(
        f"Windows x64 portable preview. Extract the entire folder and double-click {entry}.\n"
        "Offline self test: python\\python.exe -I -m novel_tts --self-test\n"
        "CPython 3.12.16 and Windows wheels are bundled. Hardware listening tests are listed in the acceptance report.\n",
        encoding="utf-8",
    )
    shutil.copy2(project / "uv.lock", bundle / "desktop-uv.lock")
    for doc in ("installation", "third-party", "local-runtime"):
        shutil.copy2(project / "docs" / (doc + ".md"), bundle / (doc + ".md"))
    args.out.mkdir(exist_ok=True, parents=True)
    dest = Path(
        shutil.make_archive(
            str(args.out / "NovelTTSReader-windows-x64-preview"),
            "zip",
            root_dir=args.stage,
            base_dir=bundle.name,
        )
    )
    with dest.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    dest.with_suffix(".zip.sha256").write_text(f"{digest}  {dest.name}\n")
    print(dest, digest, flush=True)


if __name__ == "__main__":
    main()
