from __future__ import annotations

import bisect
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QInputDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from novel_tts.application.library import Library
from novel_tts.domain.text import DisplayMap, read_draft
from novel_tts.infrastructure.paths import RESOURCES
from novel_tts.infrastructure.store import Store
from novel_tts.infrastructure.threads import Background, Repository
from novel_tts.providers.mock import MockTTS
from novel_tts.ui.player import Playback
from novel_tts.ui.editing import Editing
from novel_tts.ui.cloud import CloudSettings
from novel_tts.ui.voices import VoiceSources
from novel_tts.ui.local import LocalVoices
from novel_tts.ui.library_tools import LibraryTools
from novel_tts.ui.widgets import ImportPreview, SourceText


def button(text, fn, layout):
    widget = QPushButton(text)
    widget.clicked.connect(fn)
    layout.addWidget(widget)
    return widget


class ReaderWindow(
    LibraryTools,
    LocalVoices,
    VoiceSources,
    CloudSettings,
    Editing,
    Playback,
    QMainWindow,
):
    counter = Signal(str, int, int)
    pcm_received = Signal(object, bytes)

    def __init__(self, paths, scenario=None, force_mock=False):
        super().__init__()
        self.paths = paths
        self.setWindowTitle("小说朗读室")
        self.resize(1080, 720)
        self.repo = Repository(lambda: Store(paths))
        self.library = Library(paths, self.repo)
        self.bg = Background()
        self.bg.finished.connect(self.finished)
        self.callbacks = {}
        self.active_task = None
        self.closing = False
        self.provider = MockTTS(scenario)
        self.books, self.chapters = [], []
        self.view, self.book, self.chapter = None, "", ""
        self.generation = self.epoch = self.index = 0
        self.media = self.audio = self.media_pin = self.batch = None
        self.stream_ticket = self.stream_event = None
        self.pcm_received.connect(self.receive_pcm, Qt.ConnectionType.QueuedConnection)
        self.member_start_ms = 0
        self.intent = False
        self.waiters = []
        self.durations = {}
        self.prefetching = set()
        self.resume = None
        self.build_ui()
        self.init_editing()
        self.init_cloud()
        self.init_voices()
        self.init_local()
        if not force_mock:
            self.restore_voice_source()
        self.init_library_tools()
        playback_menu = self.menuBar().addMenu("播放")
        self.batch_action = playback_menu.addAction("连续播放同角色的已缓存短句")
        self.batch_action.setCheckable(True)
        self.counter.connect(self.count_update)
        self.clock = QTimer(self)
        self.clock.setInterval(1500)
        self.clock.timeout.connect(self.save_position)
        self.clock.start()
        self.counter_label = ""
        self.refresh_books()
        if scenario == "basic_two_chapters":
            QTimer.singleShot(250, self.demo_if_empty)

    def build_ui(self):
        self.tools = self.addToolBar("书籍操作")
        self.tools.setMovable(False)
        for name, fn in [
            ("导入 TXT", self.import_txt),
            ("打开样书", self.demo),
            ("预生成本章", self.generate),
            ("导出章节", self.export),
            ("取消任务", self.cancel_task),
        ]:
            self.tools.addAction(name).triggered.connect(fn)
        splitter = QSplitter()
        left = QWidget()
        nav = QVBoxLayout(left)
        nav.addWidget(QLabel("书架"))
        self.shelf, self.toc = QListWidget(), QListWidget()
        nav.addWidget(self.shelf, 1)
        nav.addWidget(QLabel("目录"))
        nav.addWidget(self.toc, 2)
        center = QWidget()
        reading = QVBoxLayout(center)
        self.heading = QLabel("从书架选择一本书，或打开样书")
        self.heading.setStyleSheet("font-size:20px; font-weight:600")
        reading.addWidget(self.heading)
        self.text = SourceText()
        self.text.setReadOnly(True)
        self.text.setStyleSheet("font-size:18px; padding:10px;")
        self.text.jumped.connect(self.clicked_text)
        reading.addWidget(self.text)
        pages = QHBoxLayout()
        button("上一页", lambda: self.page(-1), pages)
        self.page_label = QLabel()
        pages.addWidget(self.page_label)
        button("下一页", lambda: self.page(1), pages)
        reading.addLayout(pages)
        right = QWidget()
        self.editor = QVBoxLayout(right)
        self.editor.addWidget(QLabel("当前朗读片段"))
        self.speaker = QLabel("—")
        self.editor.addWidget(self.speaker)
        self.voice = QComboBox()
        self.editor.addWidget(self.voice)
        button("保存角色音色", self.change_voice, self.editor)
        self.origin = QLabel()
        self.origin.setWordWrap(True)
        self.editor.addWidget(self.origin)
        self.spoken = QLabel()
        self.spoken.setWordWrap(True)
        self.editor.addWidget(self.spoken)
        for label in (self.heading, self.speaker, self.origin, self.spoken):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.editor.addStretch()
        for panel in (left, center, right):
            splitter.addWidget(panel)
        splitter.setSizes([190, 640, 235])
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(splitter)
        bar = QHBoxLayout()
        button("上一句", lambda: self.seek(self.index - 1), bar)
        self.play_button = button("播放", self.toggle, bar)
        button("下一句", lambda: self.seek(self.index + 1), bar)
        button("停止", self.stop, bar)
        self.pos = QLabel("0.0 / 0.0 秒")
        bar.addWidget(self.pos)
        bar.addStretch()
        self.speed = QComboBox()
        self.speed.setToolTip(
            "倍速仅作用于播放器。此 Qt 版本的保调效果取决于系统后端。"
        )
        self.speed.addItems(["0.75", "1.0", "1.25", "1.5", "2.0"])
        self.speed.setCurrentText("1.0")
        self.speed.currentTextChanged.connect(self.speed_changed)
        bar.addWidget(QLabel("倍速"))
        bar.addWidget(self.speed)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(40)
        self.volume.setMaximumWidth(95)
        self.volume.valueChanged.connect(
            lambda v: self.audio and self.audio.setVolume(v / 100)
        )
        bar.addWidget(QLabel("音量"))
        bar.addWidget(self.volume)
        layout.addLayout(bar)
        self.setCentralWidget(body)
        self.note = QLabel("离线 Mock")
        self.statusBar().addWidget(self.note, 1)
        self.shelf.currentRowChanged.connect(self.open_book)
        self.toc.currentRowChanged.connect(self.open_chapter)

    def run(
        self,
        label,
        operation,
        done,
        *,
        guard=lambda: True,
        failed=None,
        priority=0,
        task=False,
    ):
        key = uuid.uuid4().hex
        self.callbacks[key] = (label, done, guard, failed)
        try:
            event = self.bg.start(key, operation, priority)
        except RuntimeError as exc:
            self.callbacks.pop(key)
            self.note.setText(str(exc))
            return None
        if task:
            if self.active_task:
                self.active_task.set()
            self.active_task = event
            self.task_key = key
            self.note.setText(label)

            def retry():
                if not guard():
                    self.note.setText("内容或章节已经改变，请从当前章节重新操作")
                elif (
                    QMessageBox.question(
                        self, "重试任务", "重新执行此操作？云端请求可能重复计费。"
                    )
                    == QMessageBox.StandardButton.Yes
                ):
                    self.run(
                        label,
                        operation,
                        done,
                        guard=guard,
                        failed=failed,
                        priority=priority,
                        task=True,
                    )

            self.track(key, label, retry)
        return event

    @Slot(str, object, object)
    def finished(self, key, result, error):
        callback = self.callbacks.pop(key, None)
        if not callback or self.closing:
            return
        label, done, guard, failed = callback
        self.tracked_result(key, label, error)
        if self.active_task and getattr(self, "task_key", None) == key:
            self.active_task = None
        if not guard():
            return
        if error:
            if failed:
                failed(error)
            else:
                self.note.setText(f"{label} · {error}")
            return
        done(result)

    def cancel_task(self):
        if self.active_task:
            self.active_task.set()
            self.note.setText("已请求取消")
        self.stop()

    def import_txt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入小说", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            path = Path(path)

            def decode_failed(exc):
                if not isinstance(exc, ValueError):
                    self.note.setText(str(exc))
                    return
                codec, ok = QInputDialog.getItem(
                    self,
                    "重选编码",
                    "自动解码失败，请为原文件选择编码",
                    ["gb18030", "utf-8", "utf-16-le", "utf-16-be", "big5", "cp1252"],
                    0,
                    False,
                )
                if ok:
                    self.run(
                        "读取 TXT",
                        lambda _: read_draft(path.read_bytes(), path.stem, codec),
                        self.preview,
                    )

            self.run(
                "读取 TXT",
                lambda _: read_draft(path.read_bytes(), path.stem),
                self.preview,
                failed=decode_failed,
            )

    def demo_if_empty(self):
        if not self.books:
            self.demo()

    def demo(self):
        self.run(
            "读取样书",
            lambda _: read_draft((RESOURCES / "demo.txt").read_bytes(), "灯渡"),
            self.add_draft,
        )

    def preview(self, draft):
        dialog = ImportPreview(draft, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_draft(dialog.draft)

    def add_draft(self, draft):
        self.run(
            "正在导入",
            lambda e: self.repo.call("add_book", draft, draft.boundaries, e),
            self.refresh_books,
            task=True,
        )

    @staticmethod
    def fill(widget, rows):
        widget.blockSignals(True)
        widget.clear()
        widget.addItems([title for _, title in rows])
        widget.blockSignals(False)

    def refresh_books(self, prefer=None):
        def loaded(rows):
            self.books = rows
            self.fill(self.shelf, rows)
            if rows:
                self.shelf.setCurrentRow(
                    next((i for i, row in enumerate(rows) if row[0] == prefer), 0)
                )

        self.run("加载书架", lambda _: self.repo.call("library"), loaded)

    def invalidate(self):
        self.revoke_cloud()
        self.cancel_task()
        self.generation += 1
        self.view = None
        self.resume = None

    def open_book(self, row):
        if not 0 <= row < len(self.books):
            return
        self.invalidate()
        book = self.books[row][0]
        self.book = book

        def loaded(result):
            self.chapters, self.resume = result
            self.fill(self.toc, self.chapters)
            if self.chapters:
                index = next(
                    (
                        i
                        for i, ch in enumerate(self.chapters)
                        if self.resume and ch[0] == self.resume[0]
                    ),
                    0,
                )
                self.toc.setCurrentRow(index)

        self.run(
            "加载章节",
            lambda _: (self.repo.call("toc", book), self.repo.call("progress", book)),
            loaded,
            guard=lambda: self.book == book,
        )

    def open_chapter(self, row):
        if not 0 <= row < len(self.chapters):
            return
        resume = self.resume
        self.invalidate()
        self.chapter = self.chapters[row][0]
        if resume and resume[0] != self.chapter:
            resume = None
        self.load_page(None, resume[1] if resume else 0, resume)

    def load_page(self, page=None, cp=None, resume=None, play=False):
        self.stop()
        self.generation += 1
        gen, chapter, caps = self.generation, self.chapter, self.provider.capabilities()
        self.view = None

        def lookup(_):
            n = self.repo.call("page_at", chapter, cp or 0) if page is None else page
            return self.repo.call("view", chapter, n, caps)

        def render(view):
            self.view = view
            self.heading.setText(view.title)
            self.page_label.setText(f"{view.page + 1} / {view.page_count} 页")
            self.text.setPlainText(view.source)
            self.mapping = DisplayMap(view.source, view.offset)
            self.index = max(
                0, bisect.bisect_right([line.start for line in view.lines], cp or 0) - 1
            )
            self.resume = resume
            if resume:
                self.speed.setCurrentText(str(resume[4]))
            self.paint_line()
            self.note.setText(f"本页共 {len(view.lines)} 个朗读片段")
            if play and view.lines:
                self.start()

        self.run("读取正文", lookup, render, guard=lambda: self.generation == gen)

    def page(self, direction):
        if self.view and 0 <= self.view.page + direction < self.view.page_count:
            self.load_page(self.view.page + direction)

    def change_voice(self):
        if not self.view or not self.view.lines:
            return
        view, voice, caps = (
            self.view,
            self.voice.currentText(),
            self.provider.capabilities(),
        )
        line = view.lines[self.index]
        self.stop()
        self.run(
            "保存音色",
            lambda _: self.repo.call("set_voice", view, line.speaker, caps, voice),
            lambda _: self.load_page(view.page, line.start),
            guard=lambda: self.view is view,
        )

    def clean_cache(self):
        mib, ok = QInputDialog.getInt(
            self,
            "清理缓存",
            "缓存容量上限（MiB），自动保留最近使用的音频",
            512,
            0,
            100000,
        )
        if ok:
            self.run(
                "清理缓存",
                lambda _: self.library.set_quota(mib * 1024**2),
                lambda r: self.note.setText(f"已移除 {r['removed']} 个音频文件"),
                task=True,
            )

    def generate(self):
        self.chapter_task(None)

    def export(self):
        if self.view:
            dst, _ = QFileDialog.getSaveFileName(self, "为新导出文件夹命名", "章节导出")
            if dst:
                self.chapter_task(Path(dst))

    def chapter_task(self, destination):
        if not self.view:
            return
        view, provider = self.view, self.provider
        label = "导出章节" if destination else "预生成本章"
        self.counter_label = uuid.uuid4().hex
        token = self.counter_label
        self.run(
            label,
            lambda e: self.library.chapter(
                view,
                provider,
                e,
                destination,
                lambda n, total: self.counter.emit(token, n, total),
            ),
            lambda result: self.note.setText(f"{label}完成 · {result}"),
            guard=lambda: self.view is view,
            task=True,
        )

    @Slot(str, int, int)
    def count_update(self, token, n, total):
        if token == self.counter_label:
            self.note.setText(f"已处理 {n} / {total} 个片段")

    def closeEvent(self, event):
        self.closing = True
        self.clock.stop()
        self.stop()
        self.host.close()
        self.bg.close()
        self.repo.close()
        event.accept()
