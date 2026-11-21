from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
)

from novel_tts.domain.text import read_draft


class SourceText(QTextEdit):
    jumped = Signal(int)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not self.textCursor().hasSelection():
            self.jumped.emit(
                self.cursorForPosition(event.position().toPoint()).position()
            )


class ImportPreview(QDialog):
    def __init__(self, draft, window):
        super().__init__(window)
        self.setWindowTitle("导入预览")
        self.resize(600, 510)
        self.serial = 0
        self.window = window
        form = QFormLayout()
        self.title = QLineEdit(draft.title)
        self.codec = QComboBox()
        self.codec.addItems(
            list(
                dict.fromkeys(
                    [draft.encoding, "utf-8", "utf-8-sig", "gb18030", "utf-16"]
                )
            )
        )
        self.cuts = QPlainTextEdit()
        self.cuts.setMaximumHeight(85)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.error = QLabel()
        form.addRow("书名", self.title)
        form.addRow("原文件编码", self.codec)
        form.addRow("章节起点 · 原文码点，可修改", self.cuts)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.confirm)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        for widget in (self.preview, self.error, self.buttons):
            layout.addWidget(widget)
        self.render(draft)
        self.codec.currentTextChanged.connect(self.decode_again)

    def render(self, draft):
        self.draft = draft
        self.cuts.setPlainText(", ".join(map(str, draft.boundaries)))
        self.preview.setPlainText(draft.source[:1800])
        self.error.setText(
            f"识别到 {len(draft.boundaries)} 章，共 {len(draft.source):,} 个码点。"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def decode_again(self, encoding):
        self.serial += 1
        serial, old = self.serial, self.draft
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.window.run(
            "解码预览",
            lambda _: read_draft(old.raw, old.title, encoding),
            self.render,
            guard=lambda: self.isVisible() and self.serial == serial,
            failed=lambda exc: self.error.setText(str(exc)),
        )

    def confirm(self):
        try:
            cuts = tuple(
                int(x.strip())
                for x in self.cuts.toPlainText().replace("，", ",").split(",")
            )
            if not self.title.text().strip():
                raise ValueError("请填写书名")
            if (
                not cuts
                or cuts[0] != 0
                or list(cuts) != sorted(set(cuts))
                or any(
                    x < 0
                    or x >= len(self.draft.source)
                    or (x and self.draft.source[x - 1] != "\n")
                    for x in cuts
                )
            ):
                raise ValueError("起点必须是递增的原文行首，从 0 开始")
            self.draft = replace(
                self.draft, title=self.title.text().strip(), boundaries=cuts
            )
            self.accept()
        except ValueError as exc:
            self.error.setText(str(exc))
