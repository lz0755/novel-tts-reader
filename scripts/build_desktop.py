"""Build a native standalone Qt directory using the Nuitka backend of pyside6-deploy."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["windows", "linux"], required=True)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    if platform.system().lower() != args.platform:
        parser.error("请在对应操作系统构建")
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = "NovelTTSReader"
    binary = name + (".exe" if os.name == "nt" else "")
    from PySide6.QtCore import QLibraryInfo

    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    families = [
        "platforms",
        "styles",
        "multimedia",
        "imageformats",
        "platforminputcontexts",
        "platformthemes",
        "xcbglintegrations",
        "wayland-decoration-client",
        "wayland-graphics-integration-client",
        "wayland-shell-integration",
    ]
    selected = ",".join(family for family in families if (plugins / family).is_dir())
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        f"--include-qt-plugins={selected}",
        "--include-package=keyring.backends",
        "--include-package-data=jsonschema_specifications",
        "--include-package-data=certifi",
        f"--include-data-dir={root / 'src/novel_tts/resources'}=novel_tts/resources",
        f"--include-data-dir={root / 'src/novel_tts/contracts'}=novel_tts/contracts",
        "--assume-yes-for-downloads",
        "--lto=no",
        f"--jobs={args.jobs}",
        f"--output-dir={out}",
        f"--output-filename={binary}",
        str(root / "packaging/launcher.py"),
    ]
    if os.name == "nt":
        cmd.insert(-1, "--msvc=latest")
    subprocess.run(cmd, cwd=root, check=True)
    directory = out / "launcher.dist"
    # The Qt wheel also advertises embedded EGLFS plugins; this desktop targets xcb and Wayland.
    embedded = (
        directory
        / "PySide6/qt-plugins/egldeviceintegrations/libqeglfs-kms-integration.so"
    )
    embedded.unlink(missing_ok=True)
    exe = directory / binary
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    subprocess.run([str(exe), "--self-test"], cwd=out, env=env, check=True, timeout=120)
    shutil.copy2(root / "docs/installation.md", directory / "使用说明.md")
    shutil.copy2(root / "uv.lock", directory / "desktop-uv.lock")
    shutil.copy2(root / "docs/third-party.md", directory / "THIRD-PARTY.md")
    shutil.copy2(root / "docs/local-runtime.md", directory / "本地模型说明.md")
    notices = directory / "licenses"
    for dist in metadata.distributions():
        for entry in dist.files or ():
            parts = entry.parts
            if not parts or not parts[0].endswith(".dist-info"):
                continue
            if not (
                "licenses" in parts
                or entry.name.lower().startswith(("license", "copying", "notice"))
            ):
                continue
            dest = notices.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dist.locate_file(entry), dest)
    inventory = sorted(
        (
            d.metadata["Name"],
            d.version,
            d.metadata.get("License-Expression") or d.metadata.get("License", ""),
        )
        for d in metadata.distributions()
        if d.metadata["Name"]
    )
    (directory / "build-info.json").write_text(
        json.dumps(
            dict(
                python=sys.version,
                os=platform.platform(),
                architecture=platform.machine(),
                build_environment=inventory,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if args.platform == "linux":
        for path in [directory, *directory.rglob("*")]:
            if not path.is_symlink():
                executable = path.is_dir() or bool(path.stat().st_mode & 0o100)
                path.chmod(0o755 if executable else 0o644)
    base = out / f"{name}-{args.platform}-x86_64"
    archive = Path(
        shutil.make_archive(
            str(base), "zip" if os.name == "nt" else "gztar", root_dir=directory
        )
    )
    with archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n"
    )
    print(archive, digest, flush=True)


if __name__ == "__main__":
    main()
