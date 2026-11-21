import argparse
import importlib.metadata as md
import json
import os
import platform
import shutil
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["core", "local-tts"], default="core")
    args = p.parse_args()
    names = (
        ["PySide6", "httpx", "keyring"]
        if args.mode == "core"
        else ["torch", "torchaudio", "qwen-tts", "transformers", "soundfile"]
    )
    versions = {}
    for name in names:
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = None
    info = dict(
        mode=args.mode,
        platform=platform.platform(),
        python=sys.version.split()[0],
        packages=versions,
        session=os.getenv("XDG_SESSION_TYPE"),
        nvidia_smi=bool(shutil.which("nvidia-smi")),
    )
    if args.mode == "core":
        from PySide6.QtWidgets import QApplication
        from PySide6.QtMultimedia import QMediaDevices

        app = QApplication([])
        info["qt_platform"] = app.platformName()
        info["audio_outputs"] = [d.description() for d in QMediaDevices.audioOutputs()]
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
