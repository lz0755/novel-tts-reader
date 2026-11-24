"""Build a native standalone Qt directory using the Nuitka backend of pyside6-deploy."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path


def resolve_standalone_python(python_root: Path | None = None) -> Path:
    """Return a relocatable standalone CPython 3.12 root, never a system prefix like /usr."""
    system_prefixes = {Path("/"), Path("/usr"), Path("/usr/local")}
    candidate = (python_root or Path(sys.base_prefix)).resolve()
    if os.name == "nt" or candidate not in system_prefixes:
        return candidate
    clean_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "UV_PYTHON", "CONDA_PREFIX")
    }
    find_cmd = ["uv", "python", "find", "--no-project", "--managed-python", "--system", "3.12"]
    proc = subprocess.run(find_cmd, capture_output=True, text=True, env=clean_env)
    if proc.returncode != 0 or not proc.stdout.strip():
        subprocess.run(
            ["uv", "python", "install", "--managed-python", "3.12"],
            check=True,
            env=clean_env,
        )
        proc = subprocess.run(
            find_cmd, capture_output=True, text=True, check=True, env=clean_env
        )
    py_exe = Path(proc.stdout.strip().splitlines()[-1]).resolve()
    prefix = py_exe.parents[1] if py_exe.parent.name == "bin" else py_exe.parent
    if prefix in system_prefixes:
        raise RuntimeError(f"无法解析可重定位的独立 CPython 3.12 运行时（得到系统路径 {prefix}）")
    return prefix


def stage_edge_component(
    root: Path, directory: Path, target_platform: str, *, python_root: Path | None = None
) -> Path:
    """Stage the standalone, replaceable Edge TTS component next to the desktop binary."""
    source = root / "edge_component"
    dest = directory / "edge_component"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("component.json", "pyproject.toml", "uv.lock", "README-REPLACE.md"):
        shutil.copy2(source / name, dest / name)
    shutil.copytree(
        source / "novel_tts_edge",
        dest / "novel_tts_edge",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    libs = dest / "libs"
    libs.mkdir(parents=True, exist_ok=True)
    py_platform = (
        "x86_64-pc-windows-msvc"
        if target_platform == "windows"
        else "x86_64-unknown-linux-gnu"
    )
    with tempfile.TemporaryDirectory(prefix="edge-export-") as stage:
        reqs = Path(stage) / "edge-lock.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-annotate",
                "--output-file",
                str(reqs),
            ],
            cwd=source,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python-platform",
                py_platform,
                "--python-version",
                "3.12",
                "--only-binary",
                ":all:",
                "--require-hashes",
                "--target",
                str(libs),
                "-r",
                str(reqs),
            ],
            check=True,
        )
    # Copy license files from every installed wheel in edge_component/libs.
    licenses = dest / "licenses"
    shutil.copytree(source / "licenses", licenses)
    for dist_info in libs.glob("*.dist-info"):
        for item in dist_info.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(libs)
            if (
                "licenses" in rel.parts
                or item.name.lower().startswith(("license", "copying", "notice"))
            ):
                target = licenses / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    # A cross-built Windows preview supplies the extracted Windows interpreter.
    base_python = resolve_standalone_python(python_root)
    py_dest = dest / "python"
    shutil.copytree(
        base_python,
        py_dest,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "site-packages",
            "test",
            "tests",
            "tkinter",
            "idlelib",
            "turtledemo",
            "tcl",
            "include",
            "Scripts",
            "share",
        ),
    )
    if (base_python / "LICENSE.txt").is_file():
        licenses.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_python / "LICENSE.txt", licenses / "CPYTHON-LICENSE.txt")
    return dest


def verify_staged_edge_component(comp_dir: Path) -> None:
    """Verify the staged Edge component completes handshake from its installed path."""
    py_exe = (
        comp_dir / "python/python.exe"
        if os.name == "nt"
        else comp_dir / "python/bin/python3"
    )
    if not py_exe.is_file():
        py_exe = comp_dir / "python/bin/python"
    env = dict(os.environ, PYTHONUTF8="1")
    env["PYTHONPATH"] = os.pathsep.join([str(comp_dir / "libs"), str(comp_dir)])
    proc = subprocess.run(
        [str(py_exe), "-u", "-m", "novel_tts_edge"],
        input='{"cmd":"shutdown"}\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
        timeout=20,
    )
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    hello = json.loads(first)
    if hello.get("type") != "hello" or hello.get("protocol_version") != 1:
        raise RuntimeError(f"Edge component handshake invalid: {hello!r}")


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
    stage_edge_component(root, directory, args.platform)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    with tempfile.TemporaryDirectory(
        prefix="小说 朗读室 ", dir=out, ignore_cleanup_errors=True
    ) as test_root:
        installed = Path(test_root) / "安装"
        # Windows can keep recently executed binaries open after the process exits.
        # Exercise an installation copy and package the untouched build directory.
        shutil.copytree(directory, installed, symlinks=True)
        subprocess.run(
            [str(installed / binary), "--self-test"],
            cwd=out,
            env=env,
            check=True,
            timeout=120,
        )
        verify_staged_edge_component(installed / "edge_component")
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
        f"{digest}  {archive.name}\n", encoding="ascii", newline="\n"
    )
    print(archive, digest, flush=True)


if __name__ == "__main__":
    main()
