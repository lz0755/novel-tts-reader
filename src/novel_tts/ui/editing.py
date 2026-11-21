from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
)

from novel_tts.domain.annotations import MockLLM, RuleNarration


class Editing:
    def init_editing(self):
        menu = self.menuBar().addMenu("校对")
        for name, fn in [
            ("修改选区说话人 / 风格", self.edit_part),
            ("纠正读音", self.pronounce),
            ("添加角色别名", self.alias),
            ("撤销上次标注", self.undo),
            ("用 Mock 重新标注本页", self.reanalyze),
            ("本页使用规则旁白", lambda: self.reanalyze(narrator=True)),
        ]:
            menu.addAction(name).triggered.connect(fn)

    def selected(self):
        view = self.view
        if not view or not view.lines:
            return None
        line = view.lines[self.index]
        cursor = self.text.textCursor()
        if cursor.hasSelection():
            a = self.mapping.from_qt(cursor.selectionStart())
            b = self.mapping.from_qt(cursor.selectionEnd(), True)
            return view, line, a, b
        return view, line, line.start, line.end

    def edit_form(self, title, fields):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(460, 180)
        form = QFormLayout(dialog)
        for label, widget in fields:
            form.addRow(label, widget)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        form.addRow(actions)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def mutate(self, label, view, start, fn):
        self.stop()
        self.run(
            label,
            fn,
            lambda _: self.load_page(view.page, start),
            guard=lambda: self.view is view,
            task=True,
        )

    def edit_part(self):
        selected = self.selected()
        if not selected:
            return
        view, line, a, b = selected
        people = QComboBox()
        for cid, name in view.characters:
            people.addItem(name, cid)
        people.setCurrentIndex(people.findData(line.speaker))
        name, style = QLineEdit(), QLineEdit(line.style)
        if self.edit_form(
            "人工标注",
            [
                ("原文", QLabel(view.source[a - view.offset : b - view.offset][:100])),
                ("说话人", people),
                ("或新建角色", name),
                ("表演提示", style),
            ],
        ):
            args = view, a, b, people.currentData(), style.text(), name.text()
            self.mutate(
                "保存标注", view, a, lambda _: self.repo.call("save_edit", *args)
            )

    def pronounce(self):
        selected = self.selected()
        if not selected:
            return
        view, line, a, b = selected
        line = next((l for l in view.lines if l.start <= a and b <= l.end), None)
        if line is None:
            self.note.setText("请在单句内选择纠音词")
            return
        scope = QComboBox()
        scope.addItems(["单句", "单书", "全局"])
        original = QLineEdit(view.source[a - view.offset : b - view.offset])
        replacement = QLineEdit()
        if self.edit_form(
            "纠正读音", [("范围", scope), ("原词", original), ("朗读为", replacement)]
        ):
            args = view, line, scope.currentText(), original.text(), replacement.text()
            self.mutate(
                "保存纠音", view, a, lambda _: self.repo.call("save_rule", *args)
            )

    def alias(self):
        selected = self.selected()
        if selected:
            view, line, a, _ = selected
            value, ok = QInputDialog.getText(
                self, "角色别名", dict(view.characters)[line.speaker]
            )
            if ok:
                self.mutate(
                    "保存别名",
                    view,
                    a,
                    lambda _: self.repo.call("save_alias", view, line.speaker, value),
                )

    def undo(self):
        if self.view:
            view = self.view
            self.mutate(
                "撤销标注",
                view,
                view.offset,
                lambda _: self.repo.call("undo_edit", view),
            )

    def reanalyze(self, checked=False, narrator=False):
        if self.view:
            view, caps = self.view, self.provider.capabilities()

            def analyze(event):
                req = self.repo.call("request", view.chapter, view.page)
                engine = RuleNarration() if narrator else MockLLM()
                result = engine.analyze_chunk(req, event)
                self.repo.call("commit_annotations", req, result, caps)

            self.mutate(
                "规则旁白" if narrator else "Mock 标注", view, view.offset, analyze
            )
