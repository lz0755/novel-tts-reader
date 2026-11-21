import json
import secrets
from pathlib import Path

from PySide6.QtCore import QObject,QProcess,QProcessEnvironment,QTimer,Signal
from PySide6.QtWidgets import QFileDialog,QInputDialog

from novel_tts.providers.local import MODELS,LocalTTS,client_from_hello


class ProcessHost(QObject):
    ready=Signal(object)
    failed=Signal(str)

    def __init__(self,parent):
        super().__init__(parent)
        self.process=None

    def start(self,python,settings=None,engine="qwen",cwd=None):
        if self.process:
            raise ValueError("本地服务正在运行")
        proc=QProcess(self)
        self.process=proc
        self.token=secrets.token_urlsafe(32)
        self.buffer=bytearray()
        self.handshake=False
        proc.setProgram(str(python))
        proc.setArguments(["-m","novel_tts_sidecar","--engine",engine,"--timeout","300"]+(["--settings",str(settings)] if settings else []))
        if cwd:
            proc.setWorkingDirectory(str(cwd))
        env=QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8","1")
        env.insert("HF_HUB_OFFLINE","1")
        env.insert("TRANSFORMERS_OFFLINE","1")
        proc.setProcessEnvironment(env)
        proc.started.connect(lambda:proc.write((self.token+"\n").encode()))
        proc.readyReadStandardOutput.connect(lambda:self.read(proc))
        proc.readyReadStandardError.connect(proc.readAllStandardError)
        proc.finished.connect(lambda *_:self.ended(proc))
        proc.errorOccurred.connect(lambda _:self.failed.emit("本地运行时启动或通信失败。请检查所选 Python 环境。"))
        proc.start()
        QTimer.singleShot(15000,lambda:self.expire(proc))

    def read(self,proc):
        chunk=bytes(proc.readAllStandardOutput())
        if self.handshake or self.process is not proc:
            return
        self.buffer.extend(chunk)
        if len(self.buffer)>4096:
            self.expire(proc)
        elif b"\n" in self.buffer:
            try:
                client=client_from_hello(bytes(self.buffer).splitlines()[0],self.token)
            except Exception:
                self.expire(proc)
            else:
                self.handshake=True
                self.ready.emit(client)

    def expire(self,proc):
        if self.process is proc and not self.handshake:
            self.failed.emit("本地运行时握手超时或版本不兼容")
            proc.kill()

    def ended(self,proc):
        if self.process is proc:
            self.process=None
            self.failed.emit("本地运行时已退出，已缓存的段仍可播放")
        proc.deleteLater()

    def close(self):
        proc,self.process=self.process,None
        if proc and proc.state()!=QProcess.ProcessState.NotRunning:
            proc.closeWriteChannel()
            if not proc.waitForFinished(2200):
                proc.kill()
                proc.waitForFinished(1000)


class LocalVoices:
    def init_local(self):
        self.host=ProcessHost(self)
        self.local_client=None
        self.local_target=None
        self.host.ready.connect(self.local_ready)
        self.host.failed.connect(self.local_failed)
        self.voices_menu.addSeparator()
        self.voices_menu.addAction("选择本地推理环境…",self.choose_runtime)
        for model in MODELS:
            self.voices_menu.addAction("加载 "+model.split("12Hz-")[1],lambda _=False,m=model:self.load_local(m))
        self.voices_menu.addAction("使用上次本地模型的缓存",self.local_cache)
        self.voices_menu.addAction("硬取消并卸载本地模型",self.unload_local)

    def choose_runtime(self):
        python,_=QFileDialog.getOpenFileName(self,"选取已安装 novel-tts-sidecar 的 Python")
        if python:
            cfg,_=QFileDialog.getOpenFileName(self,"选取模型准备步骤生成的 runtime.json",filter="JSON (*.json)")
            if cfg:
                self.prefs.setValue("sidecar_python",python)
                self.prefs.setValue("sidecar_settings",cfg)
                self.note.setText("本地运行时配置已保存")

    def local_failed(self,message):
        self.local_client=None
        if isinstance(self.provider,LocalTTS):
            self.provider.client=None
        self.note.setText(message)

    def load_local(self,model):
        python=self.prefs.value("sidecar_python","")
        settings=self.prefs.value("sidecar_settings","")
        if not Path(python).is_file() or not Path(settings).is_file():
            self.note.setText("本地运行时或模型未准备。请从「朗读来源」选择推理环境。")
            return
        self.cancel_task()
        self.local_target=model
        if self.host.process and self.local_client:
            self.local_ready(self.local_client)
        elif not self.host.process:
            self.note.setText("正在启动本地控制服务")
            self.host.start(python,settings)

    def local_ready(self,client):
        self.local_client=client
        target=self.local_target
        def loaded(info):
            self.prefs.setValue("local_caps",json.dumps(info))
            self.switch_voice(LocalTTS(info,client))
            self.note.setText("本地模型已加载并预热 · "+info["model"])
        self.run("正在加载并预热 "+target,lambda e:client.load(target,e),loaded,
                 guard=lambda:self.local_client is client and self.local_target==target,task=True)

    def local_cache(self):
        saved=self.prefs.value("local_caps","")
        if saved:
            self.switch_voice(LocalTTS(json.loads(saved)))
        else:
            self.note.setText("尚无已加载模型的缓存配置")

    def unload_local(self):
        self.cancel_task()
        self.host.close()
        self.local_failed("已停止本地控制服务并回收模型进程")
