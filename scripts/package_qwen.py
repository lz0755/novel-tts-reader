"""Assemble a standalone Qwen full desktop package from a core build, locked sidecar runtime, and verified weights."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from email.parser import Parser
from pathlib import Path

if __package__:
    from .build_desktop import resolve_standalone_python, verify_staged_edge_component
else:
    from build_desktop import resolve_standalone_python, verify_staged_edge_component


def _rename_with_retry(src: Path, dst: Path, attempts: int = 20, delay: float = 0.5) -> None:
    """Retry directory rename on transient Windows Defender/indexer PermissionError."""
    for attempt in range(attempts):
        try:
            src.rename(dst)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(delay)

PYTORCH_CU128_INDEX = "https://download.pytorch.org/whl/cu128"

QWEN_MODELS = {
    "0.6B": (
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "85e237c12c027371202489a0ec509ded67b5e4b5",
    ),
    "1.7B": (
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "0c0e3051f131929182e2c023b9537f8b1c68adfe",
    ),
}

DEFAULT_RUNTIME_SETTINGS = {
    "backend": "faster-qwen3-tts",
    "dtype": "bfloat16",
    "quantization": "int4",
    "attention": "sdpa",
    "tf32": True,
    "benchmark": True,
    "warmup": 2,
    "token_ceiling": 1536,
    "characters_per_second": 3.0,
    "length_margin": 1.55,
}


def verify_file_digest(path: Path, algorithm: str) -> str:
    """Compute sha256 or git_blob_sha1 matching novel_tts_sidecar.prepare.verify."""
    with path.open("rb") as stream:
        if algorithm == "sha256":
            return hashlib.file_digest(stream, "sha256").hexdigest()
        digest = hashlib.sha1(usedforsecurity=False)
        digest.update(f"blob {path.stat().st_size}\0".encode())
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
        return digest.hexdigest()


def verify_model_folder(folder: Path, repo: str, revision: str) -> dict:
    """Verify every file listed in novel-verified.json before bundling."""
    manifest_path = folder / "novel-verified.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少模型核验清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("repo"), manifest.get("revision")) != (repo, revision):
        raise ValueError(f"模型清单与目标版本不匹配：{folder}")
    files = manifest.get("files") or {}
    if not files:
        raise ValueError(f"模型清单为空：{manifest_path}")
    for rel_name, entry in files.items():
        file_path = folder / rel_name
        if not file_path.is_file():
            raise FileNotFoundError(f"缺少模型文件：{file_path}")
        actual = verify_file_digest(file_path, entry["algorithm"])
        if actual != entry["digest"]:
            raise ValueError(f"模型文件校验失败：{file_path}")
    return manifest


def _resolve_model_source(models_root: Path, key: str) -> Path:
    repo, revision = QWEN_MODELS[key]
    direct = models_root / f"{key}-{revision[:8]}"
    if direct.is_dir():
        return direct
    cfg = models_root / "runtime.json"
    if cfg.is_file():
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
        entry = (data.get("models") or {}).get(repo)
        if isinstance(entry, dict) and entry.get("path"):
            candidate = Path(entry["path"])
            if not candidate.is_absolute():
                candidate = (cfg.parent / candidate).resolve()
            if candidate.is_dir():
                return candidate
    raise FileNotFoundError(f"未在 {models_root} 找到已准备的 {key} 模型（预期 {direct.name}）")


def _extract_core_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest, filter="tar")
    else:
        raise ValueError(f"不支持的核心包归档格式：{archive}")
    # Unwrap single top-level directory (e.g. NovelTTSReader/) if present.
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not (dest / "edge_component").is_dir():
        nested = entries[0]
        if (nested / "edge_component").is_dir() or (nested / "NovelTTSReader").exists() or (nested / "NovelTTSReader.exe").exists():
            tmp = dest.parent / (dest.name + "_unwrap")
            nested.rename(tmp)
            shutil.rmtree(dest)
            tmp.rename(dest)


def populate_core_bundle(
    dest: Path,
    target_platform: str,
    *,
    core_dir: Path | None = None,
    core_archive: Path | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Copy or extract a verified core desktop build into dest."""
    if dest.exists():
        shutil.rmtree(dest)
    if core_dir is not None:
        shutil.copytree(core_dir, dest, symlinks=True)
    elif core_archive is not None:
        _extract_core_archive(core_archive, dest)
    else:
        search_root = output_dir or Path("dist")
        ext = ".zip" if target_platform == "windows" else ".tar.gz"
        candidate_archive = search_root / f"NovelTTSReader-{target_platform}-x86_64{ext}"
        candidate_dir = search_root / "launcher.dist"
        if candidate_dir.is_dir():
            shutil.copytree(candidate_dir, dest, symlinks=True)
        elif candidate_archive.is_file():
            _extract_core_archive(candidate_archive, dest)
        else:
            raise FileNotFoundError(
                f"未找到核心包目录或归档，请通过 --core-dir 或 --core-archive 指定（已检查 {candidate_dir} 与 {candidate_archive}）"
            )
    binary = "NovelTTSReader.exe" if target_platform == "windows" else "NovelTTSReader"
    if not (dest / binary).is_file():
        raise FileNotFoundError(f"核心包缺少桌面可执行文件：{dest / binary}")
    if not (dest / "edge_component/component.json").is_file():
        raise FileNotFoundError(f"核心包缺少 Edge 轻量组件：{dest / 'edge_component/component.json'}")
    return dest


def _site_packages_dir(py_dest: Path, target_platform: str) -> Path:
    if target_platform == "windows":
        return py_dest / "Lib/site-packages"
    return py_dest / "lib/python3.12/site-packages"


def _bundled_python_exe(qwen_dir: Path, target_platform: str) -> Path:
    if target_platform == "windows":
        return qwen_dir / "python/python.exe"
    for rel in ("python/bin/python3", "python/bin/python"):
        candidate = qwen_dir / rel
        if candidate.is_file():
            return candidate
    return qwen_dir / "python/bin/python3"


def _collect_dist_info_inventory_and_licenses(
    site_packages: Path, licenses_dest: Path
) -> list[list[str]]:
    inventory: list[list[str]] = []
    parser = Parser()
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        meta_path = dist_info / "METADATA"
        if meta_path.is_file():
            msg = parser.parsestr(meta_path.read_text(encoding="utf-8", errors="replace"))
            name = msg.get("Name", "")
            version = msg.get("Version", "")
            lic = msg.get("License-Expression") or msg.get("License") or ""
            if name:
                inventory.append([name, version, lic.splitlines()[0] if lic else ""])
        for item in dist_info.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(site_packages)
            if "licenses" in rel.parts or item.name.lower().startswith(
                ("license", "copying", "notice")
            ):
                target = licenses_dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    return inventory


def stage_qwen_runtime(
    root: Path,
    bundle_dir: Path,
    target_platform: str,
    models_root: Path,
    model_keys: list[str],
    *,
    python_root: Path | None = None,
    sidecar_site_packages: Path | None = None,
    verify_models: bool = True,
) -> Path:
    """Stage standalone Python 3.12 + locked sidecar/Qwen deps + verified models into bundle_dir/qwen_runtime."""
    sidecar_src = root / "sidecar"
    qwen_dir = bundle_dir / "qwen_runtime"
    if qwen_dir.exists():
        shutil.rmtree(qwen_dir)
    qwen_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stage relocatable CPython 3.12 interpreter.
    base_python = resolve_standalone_python(python_root)
    py_dest = qwen_dir / "python"
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
            "pyvenv.cfg",
        ),
    )

    # 2. Populate locked sidecar + Qwen dependencies into the bundled Python's site-packages.
    site_packages = _site_packages_dir(py_dest, target_platform)
    site_packages.mkdir(parents=True, exist_ok=True)
    if sidecar_site_packages is not None:
        shutil.copytree(
            sidecar_site_packages,
            site_packages,
            symlinks=True,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "novel_tts_sidecar",
                "novel_tts_sidecar-*.dist-info",
                "_novel_tts_sidecar*.pth",
                "__editable__*",
            ),
        )
    else:
        py_exe = _bundled_python_exe(qwen_dir, target_platform)
        with tempfile.TemporaryDirectory(prefix="qwen-venv-") as stage:
            venv_dir = Path(stage) / "venv"
            sync_env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("VIRTUAL_ENV", "UV_PYTHON", "CONDA_PREFIX")
            }
            sync_env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)
            subprocess.run(
                [
                    "uv",
                    "sync",
                    "--locked",
                    "--extra",
                    "qwen",
                    "--no-dev",
                    "--no-install-project",
                    "--python",
                    str(py_exe),
                ],
                cwd=sidecar_src,
                env=sync_env,
                check=True,
            )
            venv_site = _site_packages_dir(venv_dir, target_platform)
            shutil.copytree(
                venv_site,
                site_packages,
                symlinks=True,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

    # Always install the current tree's novel_tts_sidecar package cleanly.
    shutil.copytree(
        sidecar_src / "novel_tts_sidecar",
        site_packages / "novel_tts_sidecar",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # 3. Verify and copy pinned Qwen model weights + write portable runtime.json.
    models_dest = qwen_dir / "models"
    models_dest.mkdir(parents=True, exist_ok=True)
    licenses_dest = qwen_dir / "licenses"
    licenses_dest.mkdir(parents=True, exist_ok=True)

    models_cfg: dict[str, dict[str, str]] = {}
    bundled_models_info: list[dict[str, object]] = []
    for key in model_keys:
        repo, revision = QWEN_MODELS[key]
        src_folder = _resolve_model_source(models_root, key)
        manifest = (
            verify_model_folder(src_folder, repo, revision)
            if verify_models
            else json.loads((src_folder / "novel-verified.json").read_text(encoding="utf-8"))
        )
        folder_name = f"{key}-{revision[:8]}"
        dst_folder = models_dest / folder_name
        shutil.copytree(
            src_folder,
            dst_folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".cache"),
        )
        models_cfg[repo] = {"path": folder_name, "revision": revision}
        bundled_models_info.append(
            {
                "key": key,
                "repo": repo,
                "revision": revision,
                "relative_path": folder_name,
                "verified_files": len(manifest.get("files") or {}),
            }
        )
        for item in dst_folder.iterdir():
            if item.is_file() and item.name.lower().startswith(("license", "notice", "copying")):
                target = licenses_dest / "models" / key / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)

    runtime_settings = {"models": models_cfg, **DEFAULT_RUNTIME_SETTINGS}
    (models_dest / "runtime.json").write_text(
        json.dumps(runtime_settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 4. Licenses, lockfile, and bundle metadata.
    inventory = _collect_dist_info_inventory_and_licenses(site_packages, licenses_dest)
    if (base_python / "LICENSE.txt").is_file():
        shutil.copy2(base_python / "LICENSE.txt", licenses_dest / "CPYTHON-LICENSE.txt")
    shutil.copy2(sidecar_src / "uv.lock", qwen_dir / "qwen-uv.lock")

    git_commit = ""
    try:
        git_commit = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL, text=True
            )
            .strip()
        )
    except (OSError, subprocess.SubprocessError):
        pass

    (qwen_dir / "qwen-bundle-info.json").write_text(
        json.dumps(
            {
                "platform": target_platform,
                "architecture": platform.machine(),
                "python": sys.version,
                "source_commit": git_commit,
                "models": bundled_models_info,
                "runtime_settings": DEFAULT_RUNTIME_SETTINGS,
                "packages": inventory,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "QWEN-BUNDLE.md").write_text(
        "# 本地 Qwen 完整版说明\n\n"
        "本发行包在桌面核心版（含 Edge 轻量组件）的基础上，随包附带离线本地 Qwen3-TTS 运行时与已校验权重（`qwen_runtime/`）。\n\n"
        "## 直接离线使用\n\n"
        "1. 将整个目录解压到任意位置（支持中文与空格路径），保持相对路径不变。\n"
        "2. 启动 `NovelTTSReader`（Windows 为 `NovelTTSReader.exe`，Linux 为 `./NovelTTSReader`）。\n"
        "3. 打开 **朗读来源 → 加载 0.6B-CustomVoice**。程序会自动识别同目录下的 `qwen_runtime/`，无需手动选择路径，也不联网下载权重。\n"
        "4. 切换到 **Edge 在线语音** 时，两种来源的缓存按 `origin`（`local` 与 `cloud`）与模型指纹严格隔离。\n\n"
        "## 硬件要求与许可\n\n"
        "- 本地 Qwen 推理需要支持 CUDA 的 NVIDIA 显卡（推荐 8 GB 及以上显存，INT4 量化需计算能力 ≥ 8.0 且支持 BF16）。\n"
        "- 随包依赖锁文件见 `qwen_runtime/qwen-uv.lock`，全部第三方与模型许可见 `qwen_runtime/licenses/`。\n",
        encoding="utf-8",
    )
    return qwen_dir


def verify_staged_qwen_runtime(
    qwen_dir: Path,
    target_platform: str,
    expected_models: list[str],
    *,
    verify_imports: bool = True,
    verify_gpu: bool = False,
) -> dict:
    """Verify bundled Qwen runtime handshake, relative path resolution, imports, and optional GPU synthesis."""
    py_exe = _bundled_python_exe(qwen_dir, target_platform)
    settings_path = qwen_dir / "models/runtime.json"
    if not py_exe.is_file():
        raise FileNotFoundError(f"缺少随包 Qwen Python：{py_exe}")
    if not settings_path.is_file():
        raise FileNotFoundError(f"缺少随包 runtime.json：{settings_path}")

    env = dict(
        os.environ,
        PYTHONUTF8="1",
        PYTHONNOUSERSITE="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    # 1. Verify relative path resolution + manifest presence inside the bundled Python.
    expected_repos = [QWEN_MODELS[k][0] for k in expected_models]
    check_code = "\n".join(
        [
            "import json, pathlib, sys",
            "from novel_tts_sidecar.worker import load_settings",
            "cfg = load_settings(pathlib.Path(sys.argv[1]))",
            "expected = json.loads(sys.argv[2])",
            "resolved = {}",
            "for repo in expected:",
            "    p = pathlib.Path(cfg['models'][repo]['path'])",
            "    assert p.is_absolute() and (p / 'novel-verified.json').is_file(), f'missing {p}'",
            "    resolved[repo] = str(p)",
            "print(json.dumps({'resolved': resolved}, ensure_ascii=False))",
        ]
    )
    res = subprocess.run(
        [str(py_exe), "-c", check_code, str(settings_path), json.dumps(expected_repos)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    info = json.loads(res.stdout.splitlines()[-1])

    # 2. Verify sidecar protocol handshake with --engine mock.
    token = secrets.token_urlsafe(32)
    proc = subprocess.Popen(
        [
            str(py_exe),
            "-m",
            "novel_tts_sidecar",
            "--engine",
            "mock",
            "--settings",
            str(settings_path),
            "--timeout",
            "60",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write((token + "\n").encode("utf-8"))
        proc.stdin.flush()
        first_line = proc.stdout.readline().decode("utf-8", errors="replace").strip()
        hello = json.loads(first_line)
        if hello.get("version") != 1 or hello.get("host") != "127.0.0.1" or not hello.get("port"):
            raise RuntimeError(f"Qwen sidecar 握手无效：{hello!r}")
        info["handshake"] = hello
    finally:
        if proc.stdin:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    # 3. Verify locked Qwen stack imports cleanly without host site-packages.
    if verify_imports:
        import_code = (
            "import json, torch, qwen_tts, faster_qwen3_tts, novel_tts_sidecar; "
            "print(json.dumps({'torch': torch.__version__, 'cuda': torch.version.cuda, "
            "'cuda_available': bool(torch.cuda.is_available())}))"
        )
        imp = subprocess.run(
            [str(py_exe), "-c", import_code],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=90,
        )
        info["imports"] = json.loads(imp.stdout.splitlines()[-1])

    # 4. Optional live offline GPU synthesis verification with bundled 0.6B model.
    if verify_gpu:
        gpu_code = "\n".join(
            [
                "import json, pathlib, sys, tempfile, wave",
                "from novel_tts_sidecar.worker import load_settings",
                "from novel_tts_sidecar.qwen import Runner",
                "cfg = load_settings(pathlib.Path(sys.argv[1]))",
                "runner = Runner(cfg)",
                "cap = runner.load({'model': sys.argv[2]})",
                "out = pathlib.Path(tempfile.mkdtemp(prefix='qwen-gpu-')) / 'sample.wav'",
                "res = runner.synthesize({'model': cap['model'], 'text': '渡口起风了，灯火正亮。', 'voice': cap['voices'][0]}, out)",
                "with wave.open(str(out)) as w:",
                "    params = (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes())",
                "assert params[:3] == (24000, 1, 2) and params[3] == res['frames'] > 0",
                "print(json.dumps({'cap': {'model': cap['model'], 'gpu': cap['gpu'], 'quantization': cap['quantization'], 'backend': cap['backend']}, 'synth': res}, ensure_ascii=False))",
            ]
        )
        gpu = subprocess.run(
            [str(py_exe), "-c", gpu_code, str(settings_path), expected_repos[0]],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=300,
        )
        info["gpu_smoke"] = json.loads(gpu.stdout.splitlines()[-1])
    return info


def normalize_linux_permissions(directory: Path) -> None:
    for path in [directory, *directory.rglob("*")]:
        if not path.is_symlink():
            executable = path.is_dir() or bool(path.stat().st_mode & 0o100)
            path.chmod(0o755 if executable else 0o644)


def archive_directory(
    directory: Path, archive_base: Path, target_platform: str, *, compress_level: int = 1
) -> Path:
    """Create a Zip64 .zip (Windows) or .tar.gz (Linux) preserving relative layout and symlinks."""
    if target_platform == "windows":
        archive = archive_base.with_suffix(".zip")
        with zipfile.ZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=compress_level,
            allowZip64=True,
        ) as zf:
            for item in sorted(directory.rglob("*")):
                rel = item.relative_to(directory).as_posix()
                if item.is_dir():
                    continue
                zf.write(item, arcname=rel)
        return archive
    archive = Path(str(archive_base) + ".tar.gz")
    with tarfile.open(archive, "w:gz", compresslevel=compress_level) as tf:
        for item in sorted(directory.iterdir()):
            tf.add(item, arcname=item.name, recursive=True)
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a standalone Qwen full desktop package from a core build and verified weights."
    )
    parser.add_argument("--platform", choices=["windows", "linux"], required=True)
    parser.add_argument("--core-dir", type=Path, help="已构建的核心版目录（如 dist/launcher.dist）")
    parser.add_argument("--core-archive", type=Path, help="已构建的核心版归档（.zip 或 .tar.gz）")
    parser.add_argument("--models-root", type=Path, default=Path("build/qwen-models"))
    parser.add_argument(
        "--models", nargs="+", choices=list(QWEN_MODELS), default=["0.6B"]
    )
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--python-root", type=Path, help="独立 CPython 3.12 根目录（默认 sys.base_prefix）")
    parser.add_argument(
        "--sidecar-site-packages",
        type=Path,
        help="可选：直接复制已锁定的 sidecar site-packages（默认通过 uv export + uv pip install 安装）",
    )
    parser.add_argument("--compress-level", type=int, default=1)
    parser.add_argument("--verify-gpu", action="store_true", help="在中文空格路径内执行离线 0.6B GPU 合成验证")
    parser.add_argument("--skip-qwen-import-check", action="store_true")
    parser.add_argument("--skip-desktop-self-test", action="store_true")
    parser.add_argument("--skip-archive", action="store_true")
    parser.add_argument("--keep-stage", action="store_true")
    args = parser.parse_args(argv)

    if platform.system().lower() != args.platform:
        parser.error("请在对应操作系统构建 Qwen 完整包")

    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    models_root = (
        args.models_root if args.models_root.is_absolute() else (root / args.models_root)
    ).resolve()

    stage_dir = out / f"qwen-{args.platform}.dist"
    populate_core_bundle(
        stage_dir,
        args.platform,
        core_dir=args.core_dir.resolve() if args.core_dir else None,
        core_archive=args.core_archive.resolve() if args.core_archive else None,
        output_dir=out,
    )
    stage_qwen_runtime(
        root,
        stage_dir,
        args.platform,
        models_root,
        args.models,
        python_root=args.python_root.resolve() if args.python_root else None,
        sidecar_site_packages=(
            args.sidecar_site_packages.resolve() if args.sidecar_site_packages else None
        ),
    )

    binary = "NovelTTSReader.exe" if args.platform == "windows" else "NovelTTSReader"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    with tempfile.TemporaryDirectory(prefix="小说 朗读室 Qwen ", dir=out) as test_root:
        installed = Path(test_root) / "安装"
        _rename_with_retry(stage_dir, installed)
        try:
            if not args.skip_desktop_self_test:
                subprocess.run(
                    [str(installed / binary), "--self-test"],
                    cwd=out,
                    env=env,
                    check=True,
                    timeout=120,
                )
            verify_staged_edge_component(installed / "edge_component")
            qwen_check = verify_staged_qwen_runtime(
                installed / "qwen_runtime",
                args.platform,
                args.models,
                verify_imports=not args.skip_qwen_import_check,
                verify_gpu=args.verify_gpu,
            )
            print(json.dumps({"qwen_runtime_check": qwen_check}, ensure_ascii=False), flush=True)
        finally:
            _rename_with_retry(installed, stage_dir)

    if args.platform == "linux":
        normalize_linux_permissions(stage_dir)

    if not args.skip_archive:
        archive_base = out / f"NovelTTSReader-{args.platform}-x86_64-qwen"
        archive = archive_directory(
            stage_dir, archive_base, args.platform, compress_level=args.compress_level
        )
        with archive.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        archive.with_suffix(archive.suffix + ".sha256").write_text(
            f"{digest}  {archive.name}\n", encoding="utf-8"
        )
        print(archive, digest, flush=True)

    if not args.keep_stage and not args.skip_archive:
        shutil.rmtree(stage_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
