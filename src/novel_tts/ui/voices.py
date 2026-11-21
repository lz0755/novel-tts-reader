import json
from PySide6.QtWidgets import QMessageBox
from novel_tts.providers.local import LocalTTS

from novel_tts.providers.cloud_tts import QwenCloudTTS
from novel_tts.providers.mock import MockTTS


class VoiceSources:
    def init_voices(self):
        menu = self.menuBar().addMenu("朗读来源")
        menu.addAction("离线 Mock", lambda: self.switch_voice(MockTTS()))
        menu.addAction("配置 Qwen 云端", lambda: self.configure_profile("qwen_cloud"))
        menu.addAction("使用 Qwen 云端缓存", lambda: self.cloud_voice(False))
        menu.addAction("允许本章使用 Qwen 合成…", lambda: self.cloud_voice(True))
        self.voices_menu = menu

    def restore_voice_source(self):
        source = self.prefs.value("voice_source", "mock")
        if source == "qwen_cloud":
            self.provider = QwenCloudTTS(
                self.profiles.all()["qwen_cloud"], "", allowed=False
            )
        elif source == "local_qwen" and self.prefs.value("local_caps", ""):
            self.provider = LocalTTS(json.loads(self.prefs.value("local_caps")))

    def revoke_cloud(self):
        if isinstance(self.provider, QwenCloudTTS):
            self.provider.allowed.clear()

    def switch_voice(self, provider):
        self.cancel_task()
        self.revoke_cloud()
        self.provider = provider
        self.prefs.setValue("voice_source", provider.capabilities().provider)
        if self.view:
            view = self.view
            cp = view.lines[self.index].start if view.lines else view.offset
            self.load_page(view.page, cp)

    def cloud_voice(self, allow):
        if not self.view:
            self.note.setText("请先选择书与章节")
            return
        p = self.profiles.all()["qwen_cloud"]
        view = self.view
        if allow:
            answer = QMessageBox.question(
                self,
                "允许本章云端配音",
                f"将《{view.title}》缺少缓存的朗读文本发送到 {p.endpoint}。\n"
                f"模型 {p.model}，包括接下来预取的句子，最多 {p.budget} 次付费请求（含重试），费用待估。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        def create(_):
            secret = self.profiles.credential(p) if allow else ""
            return QwenCloudTTS(
                p,
                secret,
                allowed=allow,
                record=lambda r: self.repo.call("record_attempt", view.book, r),
            )

        self.run(
            "切换云端来源", create, self.switch_voice, guard=lambda: self.view is view
        )
