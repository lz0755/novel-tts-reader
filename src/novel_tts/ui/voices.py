import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from novel_tts.domain.voices import voice_label
from novel_tts.providers.cloud_tts import QwenCloudTTS
from novel_tts.providers.edge_tts import (
    EdgeTTS,
    EdgeVoiceCatalog,
    EdgeWorkerRuntime,
)
from novel_tts.providers.local import LocalTTS
from novel_tts.providers.mock import MockTTS


class VoiceSources:
    def init_voices(self):
        self.edge_catalog = EdgeVoiceCatalog(self.paths.data)
        self.edge_runtime = EdgeWorkerRuntime()
        menu = self.menuBar().addMenu("朗读来源")
        menu.addAction("离线 Mock", lambda: self.switch_voice(MockTTS()))
        menu.addSeparator()
        menu.addAction("Edge 在线语音（仅用缓存）", lambda: self.edge_voice(False))
        menu.addAction("允许本章使用 Edge 合成…", lambda: self.edge_voice(True))
        menu.addAction("撤销本章 Edge 授权", self.revoke_edge_action)
        menu.addAction("管理 Edge 音色与目录…", self.manage_edge_voices)
        menu.addSeparator()
        menu.addAction("配置 Qwen 云端", lambda: self.configure_profile("qwen_cloud"))
        menu.addAction("使用 Qwen 云端缓存", lambda: self.cloud_voice(False))
        menu.addAction("允许本章使用 Qwen 合成…", lambda: self.cloud_voice(True))
        self.voices_menu = menu

    def _make_edge_provider(self, *, allowed=False, book="", chapter=""):
        record = (
            (lambda row, b=book: self.repo.call("record_attempt", b, row))
            if book
            else None
        )
        return EdgeTTS(
            self.edge_runtime,
            self.edge_catalog,
            allowed=allowed,
            book=book,
            chapter=chapter,
            record=record,
        )

    def restore_voice_source(self):
        source = self.prefs.value("voice_source", "mock")
        if source == "edge_tts":
            self.provider = self._make_edge_provider(allowed=False)
        elif source == "qwen_cloud":
            self.provider = QwenCloudTTS(
                self.profiles.all()["qwen_cloud"], "", allowed=False
            )
        elif source == "local_qwen" and self.prefs.value("local_caps", ""):
            self.provider = LocalTTS(json.loads(self.prefs.value("local_caps")))

    def revoke_cloud(self, *, switching_source=False):
        if isinstance(self.provider, QwenCloudTTS):
            self.provider.allowed.clear()
        elif isinstance(self.provider, EdgeTTS):
            self.provider.revoke()
            if not switching_source:
                self.provider = self._make_edge_provider(allowed=False)

    def switch_voice(self, provider):
        self.cancel_task()
        self.revoke_cloud(switching_source=True)
        self.provider = provider
        self.prefs.setValue("voice_source", provider.capabilities().provider)
        if self.view:
            view = self.view
            cp = view.lines[self.index].start if view.lines else view.offset
            self.load_page(view.page, cp)

    def edge_voice(self, allow):
        if not allow:
            self.switch_voice(self._make_edge_provider(allowed=False))
            self.note.setText("Edge 在线语音（仅用缓存 · 在线、无需用户配置 API Key）")
            return
        if not self.view:
            self.note.setText("请先选择书与章节")
            return
        self.prompt_edge_authorization()

    def _authorize_edge_chapter(self, view):
        provider = self._make_edge_provider(allowed=True, book=view.book, chapter=view.chapter)
        if isinstance(self.provider, EdgeTTS):
            self.provider.revoke()
            self.provider = provider
            self.paint_line()
        else:
            self.switch_voice(provider)
        self.note.setText(f"已允许《{view.title}》发送到微软合成（含本章预取与预生成）")

    def prompt_edge_authorization(self, *, on_authorized=None, reason=""):
        view = self.view
        if not view:
            self.note.setText("请先选择书与章节")
            return False
        box = QMessageBox(self)
        box.setWindowTitle("允许本章发送到微软合成")
        box.setIcon(QMessageBox.Icon.Question)
        prefix = f"{reason}。\n\n" if reason else ""
        box.setText(
            f"{prefix}将《{view.title}》缺少缓存的正文发送到微软在线语音服务合成"
            "（在线、无需用户配置 API Key）。\n"
            "本次授权覆盖当前书、当前章、当前 Edge 来源的播放、后台预取和预生成；"
            "切书、切章、切来源或重启后自动恢复仅用缓存。"
        )
        allow_btn = box.addButton(
            "允许本章发送到微软合成", QMessageBox.ButtonRole.AcceptRole
        )
        cache_btn = box.addButton("仅用缓存", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cache_btn)
        box.exec()
        if box.clickedButton() is allow_btn:
            if self.view is not view:
                self.note.setText("章节已切换，未授予旧章节权限")
                return False
            self._authorize_edge_chapter(view)
            if on_authorized is not None:
                on_authorized()
            return True
        if reason:
            self.note.setText(f"{reason}，已保持仅用缓存")
        return False

    def revoke_edge_action(self):
        if not isinstance(self.provider, EdgeTTS):
            self.note.setText("当前朗读来源不是 Edge 在线语音")
            return
        self.cancel_task()
        self.revoke_cloud(switching_source=False)
        if self.view:
            self.paint_line()
        self.note.setText("已撤销本章 Edge 授权，恢复仅用缓存")

    def manage_edge_voices(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("管理 Edge 在线音色")
        dialog.resize(620, 480)
        layout = QVBoxLayout(dialog)
        info = QLabel(
            "Edge 在线语音：在线、无需用户配置 API Key。\n"
            "手动刷新目录仅请求音色元数据，不会发送正文，也不改变本章正文授权状态。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        status = QLabel()
        layout.addWidget(status)

        voice_list = QListWidget()
        layout.addWidget(voice_list, 1)

        narrator_row = QHBoxLayout()
        narrator_row.addWidget(QLabel("默认旁白音色"))
        narrator_box = QComboBox()
        narrator_row.addWidget(narrator_box, 1)
        save_narrator_btn = QPushButton("保存旁白音色")
        narrator_row.addWidget(save_narrator_btn)
        layout.addLayout(narrator_row)

        custom_row = QHBoxLayout()
        custom_input = QLineEdit()
        custom_input.setPlaceholderText("输入自定义音色 ShortName，例如 zh-HK-HiuMaanNeural")
        add_custom_btn = QPushButton("添加自定义音色")
        remove_custom_btn = QPushButton("移除所选自定义音色")
        custom_row.addWidget(custom_input, 1)
        custom_row.addWidget(add_custom_btn)
        custom_row.addWidget(remove_custom_btn)
        layout.addLayout(custom_row)

        action_row = QHBoxLayout()
        refresh_btn = QPushButton("手动刷新音色目录")
        close_btn = QPushButton("关闭")
        action_row.addWidget(refresh_btn)
        action_row.addStretch()
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)

        changed = [False]

        def populate():
            entries = self.edge_catalog.all_entries()
            refreshed_at = self.edge_catalog.refreshed_at()
            status.setText(
                f"当前共 {len(entries)} 个可用音色"
                + (f" · 上次刷新：{refreshed_at}" if refreshed_at else " · 使用内置中文预设")
            )
            voice_list.clear()
            narrator_box.clear()
            for entry in entries:
                short = entry["short_name"]
                label = voice_label("edge_tts", short, entry)
                if entry.get("custom"):
                    label += "（自定义）"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, short)
                item.setData(Qt.ItemDataRole.UserRole + 1, bool(entry.get("custom")))
                voice_list.addItem(item)
                narrator_box.addItem(label, short)
            idx = narrator_box.findData(self.edge_catalog.narrator())
            if idx >= 0:
                narrator_box.setCurrentIndex(idx)

        def save_narrator():
            voice = narrator_box.currentData()
            if not voice:
                return
            try:
                self.edge_catalog.set_narrator(voice)
                changed[0] = True
                status.setText(f"默认旁白音色已更新为 {voice}")
            except ValueError as exc:
                status.setText(str(exc))

        def add_custom():
            try:
                added = self.edge_catalog.add_custom_voice(custom_input.text())
                custom_input.clear()
                changed[0] = True
                populate()
                status.setText(f"已添加自定义音色：{added}")
            except ValueError as exc:
                status.setText(str(exc))

        def remove_custom():
            item = voice_list.currentItem()
            if not item or not item.data(Qt.ItemDataRole.UserRole + 1):
                status.setText("请选择一个标记为「自定义」的音色移除")
                return
            short = item.data(Qt.ItemDataRole.UserRole)
            self.edge_catalog.remove_custom_voice(short)
            changed[0] = True
            populate()
            status.setText(f"已移除自定义音色：{short}")

        def refresh_catalog():
            refresh_btn.setEnabled(False)
            status.setText("正在请求 Edge 在线音色目录…")

            def fetch(event):
                voices = self.edge_runtime.list_voices(event)
                return self.edge_catalog.update_catalog(voices)

            def done(count):
                refresh_btn.setEnabled(True)
                changed[0] = True
                populate()
                status.setText(f"音色目录刷新成功，共 {count} 个在线音色（已保留自定义音色与角色绑定）")

            def failed(exc):
                refresh_btn.setEnabled(True)
                status.setText(f"刷新失败，沿用已有目录与内置预设 · {exc}")

            self.run(
                "刷新 Edge 音色目录",
                fetch,
                done,
                guard=lambda: dialog.isVisible(),
                failed=failed,
            )

        save_narrator_btn.clicked.connect(save_narrator)
        add_custom_btn.clicked.connect(add_custom)
        remove_custom_btn.clicked.connect(remove_custom)
        refresh_btn.clicked.connect(refresh_catalog)
        close_btn.clicked.connect(dialog.accept)

        populate()
        dialog.exec()
        if changed[0] and isinstance(self.provider, EdgeTTS) and self.view:
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
