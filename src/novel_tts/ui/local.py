import json
import os
import secrets
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QFileDialog

from novel_tts.providers.local import MODELS, LocalTTS, client_from_hello


def runtime_paths(prefs, directory):
    python = Path(prefs.value("sidecar_python", ""))
    settings = Path(prefs.value("sidecar_settings", ""))
    if python.is_file() and settings.is_file():
        return python, settings
    bundle = directory / "local-qwen"
    python = bundle / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    settings = bundle / "models" / "runtime.json"
    if python.is_file() and settings.is_file():
        return python, settings
    return None


class ProcessHost(QObject):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.process = None

    def start(self, python, settings=None, engine="qwen", cwd=None):
        if self.process:
            raise ValueError("本地服务正在运行")
        proc = QProcess(self)
        self.process = proc
        self.token = secrets.token_urlsafe(32)
        self.buffer = bytearray()
        self.handshake = False
        proc.setProgram(str(python))
        proc.setArguments(
            ["-m", "novel_tts_sidecar", "--engine", engine, "--timeout", "300"]
            + (["--settings", str(settings)] if settings else [])
        )
        if cwd:
            proc.setWorkingDirectory(str(cwd))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("HF_HUB_OFFLINE", "1")
        env.insert("TRANSFORMERS_OFFLINE", "1")
        env.insert("PYTHONNOUSERSITE", "1")
        proc.setProcessEnvironment(env)
        proc.started.connect(lambda: proc.write((self.token + "\n").encode()))
        proc.readyReadStandardOutput.connect(lambda: self.read(proc))
        proc.readyReadStandardError.connect(proc.readAllStandardError)
        proc.finished.connect(lambda *_: self.ended(proc))

        def on_error(kind):
            if kind == QProcess.ProcessError.FailedToStart and self.process is proc:
                self.process = None
                proc.deleteLater()
            self.failed.emit("本地运行时启动或通信失败。请检查所选 Python 环境。")

        proc.errorOccurred.connect(on_error)
        proc.start()
        QTimer.singleShot(15000, lambda: self.expire(proc))

    def read(self, proc):
        chunk = bytes(proc.readAllStandardOutput())
        if self.handshake or self.process is not proc:
            return
        self.buffer.extend(chunk)
        if len(self.buffer) > 4096:
            self.expire(proc)
        elif b"\n" in self.buffer:
            try:
                client = client_from_hello(
                    bytes(self.buffer).splitlines()[0], self.token
                )
            except Exception:
                self.expire(proc)
            else:
                self.handshake = True
                self.ready.emit(client)

    def expire(self, proc):
        if self.process is proc and not self.handshake:
            self.failed.emit("本地运行时握手超时或版本不兼容")
            proc.kill()

    def ended(self, proc):
        if self.process is proc:
            self.process = None
            self.failed.emit("本地运行时已退出，已缓存的段仍可播放")
        proc.deleteLater()

    def close(self):
        proc, self.process = self.process, None
        if proc and proc.state() != QProcess.ProcessState.NotRunning:
            proc.closeWriteChannel()
            if not proc.waitForFinished(2200):
                proc.kill()
                proc.waitForFinished(1000)


class LocalVoices:
    def init_local(self):
        self.host = ProcessHost(self)
        self.local_client = None
        self.pending_local_load = None
        self.host.ready.connect(self.local_ready)
        self.host.failed.connect(self.local_failed)
        self.voices_menu.addSeparator()
        self.voices_menu.addAction("选择本地推理环境…", self.choose_runtime)
        modes = {"config": "使用 runtime.json", "int4": "INT4", "none": "关闭量化"}
        self.local_quantization = self.prefs.value("local_quantization", "config")
        if self.local_quantization not in modes:
            self.local_quantization = "config"
        menu = self.voices_menu.addMenu("本地权重量化")
        # PySide 6.9 can dispose a submenu when a temporary QAction.menu()
        # wrapper is collected. Keep the owning action alive with the window.
        self._quantization_menu_action = menu.menuAction()
        group = QActionGroup(menu)
        for mode, label in modes.items():
            action = menu.addAction(label)
            group.addAction(action)
            action.setCheckable(True)
            action.setChecked(mode == self.local_quantization)
            action.triggered.connect(lambda _, m=mode: self.set_local_quantization(m))
        for model in MODELS:
            self.voices_menu.addAction(
                "加载 " + model.split("12Hz-")[1],
                lambda _=False, m=model: self.load_local(m),
            )
        self.voices_menu.addAction("使用上次本地模型的缓存", self.local_cache)
        self.voices_menu.addAction("硬取消并卸载本地模型", self.unload_local)

    def set_local_quantization(self, mode):
        self.local_quantization = mode
        self.prefs.setValue("local_quantization", mode)
        if self.host.process and self.pending_local_load is not None:
            self.load_local(self.pending_local_load[0])
        elif isinstance(self.provider, LocalTTS):
            self.load_local(self.provider.info["model"])
        else:
            self.note.setText("已选择本地权重量化，加载本地模型后生效")

    def choose_runtime(self):
        python, _ = QFileDialog.getOpenFileName(
            self, "选取已安装 novel-tts-sidecar 的 Python"
        )
        if python:
            cfg, _ = QFileDialog.getOpenFileName(
                self, "选取模型准备步骤生成的 runtime.json", filter="JSON (*.json)"
            )
            if cfg:
                saved = self.prefs.value("sidecar_python"), self.prefs.value("sidecar_settings")
                if self.host.process and (python, cfg) != saved:
                    self.unload_local()
                self.prefs.setValue("sidecar_python", python)
                self.prefs.setValue("sidecar_settings", cfg)
                self.note.setText("本地运行时配置已保存")

    def local_failed(self, message):
        self.pending_local_load = None
        self.local_client = None
        if isinstance(self.provider, LocalTTS):
            self.provider.client = None
        self.note.setText(message)

    def load_local(self, model):
        paths = runtime_paths(self.prefs, Path(QCoreApplication.applicationFilePath()).parent)
        if paths is None:
            self.note.setText("本地运行时或模型未准备。请从「朗读来源」选择推理环境。")
            return
        python, settings = paths
        self.cancel_task()
        self.pending_local_load = (model, self.local_quantization)
        if self.host.process and self.local_client:
            self.local_ready(self.local_client)
        elif not self.host.process:
            self.note.setText("正在启动本地控制服务")
            self.host.start(python, settings)

    def local_ready(self, client):
        self.local_client = client
        pending = self.pending_local_load
        if pending is None:
            return
        target, mode = pending

        def loaded(info):
            self.pending_local_load = None
            saved = {k: v for k, v in info.items() if k != "stream_session"}
            self.prefs.setValue("local_caps", json.dumps(saved))
            self.switch_voice(LocalTTS(info, client))
            precision = (
                "INT4" if info.get("quantization") == "int4" else info.get("dtype")
            )
            message = "本地模型已加载并预热 · " + info["model"]
            if info.get("backend") == "faster-qwen3-tts":
                message += " · Faster 流式"
            self.note.setText(f"{message} · {precision}" if precision else message)

        def failed(error):
            self.pending_local_load = None
            if isinstance(self.provider, LocalTTS):
                self.provider.client = None
            self.note.setText(f"本地模型加载失败 · {error}")

        self.run(
            "正在加载并预热 " + target,
            lambda e: client.load(target, e, None if mode == "config" else mode),
            loaded,
            guard=lambda: self.local_client is client and self.pending_local_load is pending,
            failed=failed,
            task=True,
        )

    def local_cache(self):
        saved = self.prefs.value("local_caps", "")
        if saved:
            self.switch_voice(LocalTTS(json.loads(saved)))
        else:
            self.note.setText("尚无已加载模型的缓存配置")

    def unload_local(self):
        self.cancel_task()
        self.host.close()
        self.local_failed("已停止本地控制服务并回收模型进程")
