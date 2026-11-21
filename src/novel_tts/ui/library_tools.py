from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LibraryTools:
    def init_library_tools(self):
        menu = self.menuBar().addMenu("书库")
        menu.addAction("备份数据库…", self.backup)
        menu.addAction("删除当前书籍…", self.delete_selected)
        menu.addAction("清理缓存…", self.clean_cache)
        self.task_dock = QDockWidget("任务记录", self)
        self.task_rows = {}
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.task_list = QListWidget()
        layout.addWidget(self.task_list)
        bar = QHBoxLayout()
        for label, fn in (
            ("重试所选任务", self.retry_selected),
            ("取消运行中的任务", self.cancel_task),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            bar.addWidget(btn)
        layout.addLayout(bar)
        self.task_dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.task_dock)
        self.task_dock.hide()
        self.menuBar().addAction(self.task_dock.toggleViewAction())

    def track(self, key, label, retry):
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(f"{label} · 运行中")
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.task_list.insertItem(0, item)
        self.task_rows[key] = (item, retry, False)
        if self.task_list.count() > 100:
            old = self.task_list.takeItem(self.task_list.count() - 1)
            self.task_rows.pop(old.data(Qt.ItemDataRole.UserRole), None)

    def tracked_result(self, key, label, error):
        if key in self.task_rows:
            item, retry, _ = self.task_rows[key]
            item.setText(f"{label} · {error if error else '已完成'}")
            self.task_rows[key] = (item, retry, bool(error))

    def retry_selected(self):
        item = self.task_list.currentItem()
        if item:
            _, retry, failed = self.task_rows[item.data(Qt.ItemDataRole.UserRole)]
            if failed:
                retry()

    def backup(self):
        dst, _ = QFileDialog.getSaveFileName(
            self, "备份到新的 SQLite 文件", "书库备份.sqlite3"
        )
        if not dst:
            return

        def copy(_):
            path = Path(dst)
            # Reserve a new file exclusively so a previous backup cannot be overwritten.
            with path.open("xb"):
                pass
            try:
                self.repo.call("backup", str(path))
            except Exception:
                path.unlink(missing_ok=True)
                raise

        self.run("备份书库", copy, lambda _: self.note.setText("备份完成"), task=True)

    def delete_selected(self):
        if not self.view:
            return
        view = self.view
        title = next(label for key, label in self.books if key == view.book)
        if (
            QMessageBox.question(self, "删除书籍", f"删除《{title}》及其校对与进度？")
            == QMessageBox.StandardButton.Yes
        ):
            self.invalidate()

            def done(_):
                self.text.clear()
                self.toc.clear()
                self.heading.setText("从书架选择一本书，或打开样书")
                self.note.setText("书籍已删除")
                self.refresh_books()

            self.run(
                "删除书籍",
                lambda _: self.repo.call("delete_book", view.book),
                done,
                task=True,
            )
