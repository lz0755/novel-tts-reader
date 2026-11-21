from __future__ import annotations

import hashlib
import json
import os
import tempfile
import wave
from pathlib import Path

from novel_tts.domain.models import AudioArtifact, Cancelled

RATE = 24000


def inspect_wav(path, request):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(131072), b""):
            digest.update(block)
    with wave.open(str(path), "rb") as wav:
        rate, channels, width, frames = (
            wav.getframerate(),
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getnframes(),
        )
        if (rate, channels, width) != (RATE, 1, 2) or not (0 < frames <= RATE * 1800):
            raise ValueError("音频格式、采样率或长度无效")
        remaining = frames
        while remaining:
            data = wav.readframes(min(remaining, 65536))
            if not data or len(data) % 2:
                raise ValueError("WAV 被截断")
            remaining -= len(data) // 2
    return AudioArtifact(
        Path(path), digest.hexdigest(), frames, rate, channels, width, request
    )


def publish_directory(stage, output):
    # Both supported platforms have a race-free no-replace directory rename.
    if os.name == "nt":
        stage.rename(output)
        return
    import ctypes
    import errno

    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, os.fsencode(stage), -100, os.fsencode(output), 1)
        if result == 0:
            return
        error = ctypes.get_errno()
        if error not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise OSError(error, os.strerror(error), str(output))
    # Other filesystems reserve a fresh directory first and publish the manifest last.
    output.mkdir()
    try:
        (stage / "chapter.wav").rename(output / "chapter.wav")
        (stage / "manifest.json").rename(output / "manifest.json")
    except OSError:
        (output / "chapter.wav").unlink(missing_ok=True)
        output.rmdir()
        raise


def export_chapter(directory, title, chapter, segments, cancel, source=None):
    """Publish the complete WAV and manifest as one new directory."""
    import shutil

    output = Path(directory) / title
    if output.exists():
        raise FileExistsError("导出位置已存在，请选择新的名称")
    stage = Path(tempfile.mkdtemp(prefix=".novel-export-", dir=output.parent))
    try:
        listing = []
        frame = 0
        with wave.open(str(stage / "chapter.wav"), "wb") as out:
            out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            for line, audio in segments:
                if cancel.is_set():
                    raise Cancelled()
                checked = inspect_wav(audio.path, audio.request)
                if checked.sha256 != audio.sha256:
                    raise ValueError("缓存摘要已改变")
                with wave.open(str(audio.path), "rb") as wav:
                    while block := wav.readframes(65536):
                        out.writeframesraw(block)
                listing.append(
                    dict(
                        start_cp=line.start,
                        end_cp=line.end,
                        frame=frame,
                        frames=audio.frames,
                        sha256=audio.sha256,
                        fingerprint=audio.request.key,
                        speaker=line.speaker,
                        provider=audio.request.provider,
                        model=audio.request.model,
                        revision=audio.request.model_revision,
                        voice=audio.request.voice,
                        origin=audio.request.origin,
                    )
                )
                frame += audio.frames
        if not listing:
            raise ValueError("本章没有可朗读片段")
        manifest = dict(
            version=1,
            chapter=chapter,
            source=source or {},
            canonical_offset_unit="chapter_codepoints",
            rate=RATE,
            channels=1,
            width=2,
            frames=frame,
            complete=True,
            segments=listing,
        )
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if cancel.is_set():
            raise Cancelled()
        if output.exists():
            raise FileExistsError("目标已存在")
        publish_directory(stage, output)
        return output
    finally:
        if stage.exists():
            shutil.rmtree(stage)
