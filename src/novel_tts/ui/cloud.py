from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from novel_tts.application.analysis import analyze_chapter
from novel_tts.application.costs import estimate, pricing_key
from novel_tts.infrastructure.profiles import Profiles
from novel_tts.providers.llm import RemoteLLM


class CloudSettings:
    def init_cloud(self):
        self.profiles = Profiles(self.paths.data)
        self.prefs = QSettings(
            str(self.paths.data / "preferences.ini"), QSettings.Format.IniFormat
        )
        self.llm_name = self.prefs.value("llm", "openai")
        menu = self.menuBar().addMenu("供应商")
        menu.addAction("配置小说分析服务", self.configure_llm)
        menu.addAction("测试模型列表连接", self.probe_connection)
        menu.addAction("以固定样本测试标注能力…", self.probe_schema)
        menu.addAction("云端标注本章…", self.analyze_cloud)
        menu.addAction("导入十章费用样本…", self.import_cost_sample)
        menu.addAction("批量标注整本书…", self.analyze_book)
        menu.addAction("请求与费用记录", self.show_attempts)
        self.provider_menu = menu

    def probe_connection(self):
        import httpx

        p = self.profiles.all()[self.llm_name]

        def probe(_):
            key = self.profiles.credential(p)
            resp = httpx.get(
                p.endpoint.rstrip("/") + "/models",
                headers={"Authorization": "Bearer " + key} if key else {},
                timeout=10,
            )
            return f"模型列表 HTTP {resp.status_code}，模型 ID 可手工填写"

        self.run("连接探测", probe, self.note.setText)

    def probe_schema(self):
        from novel_tts.domain.models import AnnotationRequest, Parent

        p = self.profiles.all()[self.llm_name]
        if (
            QMessageBox.question(
                self,
                "固定样本能力测试",
                f"向 {p.endpoint} 发送原创测试句「灯亮了。」并验证标注？此请求可能计费。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def probe(event):
            req = AnnotationRequest(
                "probe",
                "sample",
                "v1",
                0,
                "probe-capability",
                (Parent("p0", 0, "灯亮了。"),),
            )
            api = RemoteLLM(
                p,
                self.profiles.credential(p),
                record=lambda r: self.repo.call("record_attempt", "probe", r),
            )
            result = api.analyze_chunk(req, event)
            return f"样本原文覆盖和引用校验通过 · {result.model}"

        self.run("标注能力探测", probe, self.note.setText, task=True)

    def configure_llm(self):
        choices = ["openai", "deepseek", "custom"]
        index = choices.index(self.llm_name) if self.llm_name in choices else 0
        name, ok = QInputDialog.getItem(
            self, "小说分析", "选择配置", choices, index, False
        )
        if ok:
            self.configure_profile(name)

    def configure_profile(self, name):
        profiles = self.profiles.all()
        p = profiles[name]
        endpoint, model = QLineEdit(p.endpoint), QLineEdit(p.model)
        protocol = QComboBox()
        protocol.addItems(
            ["qwen_audio_http"]
            if name == "qwen_cloud"
            else ["responses", "chat_completions"]
        )
        protocol.setCurrentText(p.protocol)
        output = QComboBox()
        output.addItems(["json_schema", "json_object", "text"])
        output.setCurrentText(p.output)
        voices = QLineEdit(", ".join(p.voices))
        budget = QSpinBox()
        budget.setRange(1, 10000)
        budget.setValue(p.budget)
        secret = QLineEdit()
        secret.setEchoMode(QLineEdit.EchoMode.Password)
        secret.setPlaceholderText("留空以使用现有凭据")
        remember = QCheckBox("将新密钥保存到系统凭据服务")
        fields = [
            ("服务端点", endpoint),
            ("模型 ID", model),
            ("协议", protocol),
            ("音色 ID（逗号分隔）", voices)
            if name == "qwen_cloud"
            else ("模型输出能力", output),
            ("本次操作请求上限（含重试）", budget),
            ("API 密钥", secret),
            ("", remember),
        ]
        currency = QLineEdit(p.currency)
        date = QLineEdit(p.price_date)
        incoming = QLineEdit(
            "" if p.input_per_million is None else str(p.input_per_million)
        )
        outgoing = QLineEdit(
            "" if p.output_per_million is None else str(p.output_per_million)
        )
        checked = QCheckBox("已为当前模型和音色验证 instruction 能力")
        checked.setChecked(p.instruct)
        fields.extend(
            [
                ("价格日期（YYYY-MM-DD）", date),
                ("币种", currency),
                (
                    "每百万" + ("字符" if name == "qwen_cloud" else "输入 token"),
                    incoming,
                ),
            ]
        )
        fields.append(
            ("", checked) if name == "qwen_cloud" else ("每百万输出 token", outgoing)
        )
        dialog = self.form_dialog("配置 " + name, fields)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            prices = [
                float(widget.text()) if widget.text().strip() else None
                for widget in (incoming, outgoing)
            ]
            if any(n is not None and (not 0 <= n < 1e9) for n in prices):
                raise ValueError()
            if any(n is not None for n in prices):
                from datetime import date as day

                day.fromisoformat(date.text().strip())
                if not currency.text().strip():
                    raise ValueError()
        except ValueError:
            self.note.setText("请填写有效的非负价格、日期与币种")
            return
        updated = replace(
            p,
            input_per_million=prices[0],
            output_per_million=prices[1],
            currency=currency.text().strip(),
            price_date=date.text().strip(),
            instruct=checked.isChecked(),
            endpoint=endpoint.text().strip(),
            model=model.text().strip(),
            protocol=protocol.currentText(),
            output=output.currentText(),
            voices=tuple(v.strip() for v in voices.text().split(",") if v.strip()),
            budget=budget.value(),
        )
        key, persist = secret.text(), remember.isChecked()

        def saved(message):
            if name != "qwen_cloud":
                self.llm_name = name
                self.prefs.setValue("llm", name)
            self.note.setText(message)

        self.run(
            "保存供应商配置", lambda _: self.profiles.save(updated, key, persist), saved
        )

    def analyze_cloud(self):
        if not self.view:
            return
        view, caps = self.view, self.provider.capabilities()
        p = self.profiles.all()[self.llm_name]
        if (
            QMessageBox.question(
                self,
                "允许发送本章正文",
                f"将《{view.title}》的正文、最多 256 码点前文及本书角色名发送到 {p.endpoint}。\n"
                f"模型 {p.model}，用于标注角色和台词。最多 {p.budget} 次请求（含重试），费用待估。是否继续？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.stop()

        def analysis(event):
            remote = RemoteLLM(
                p,
                self.profiles.credential(p),
                record=lambda row: self.repo.call("record_attempt", view.book, row),
            )
            return analyze_chapter(self.repo, view, caps, remote, event)

        def complete(n):
            self.note.setText(f"已验证并提交 {n} 批标注")
            self.load_page(view.page, view.offset)

        def interrupted(exc):
            self.load_page(view.page, view.offset)
            self.note.setText(f"云端标注中止 · {exc}")

        self.run(
            "分析本章",
            analysis,
            complete,
            guard=lambda: self.view is view,
            failed=interrupted,
            task=True,
        )

    def analyze_book(self):
        if not self.view:
            return
        view = self.view
        self.run(
            "准备批量预算",
            lambda _: self.repo.call("book_length", view.book),
            lambda size: self.confirm_book_analysis(view, size),
            guard=lambda: self.view is view,
        )

    def import_cost_sample(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 cost_summary 生成的费用样本", filter="JSON (*.json)"
        )
        if not path:
            return
        profile = self.profiles.all()[self.llm_name]

        def read_sample(_):
            with Path(path).open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("费用样本超过 64 KiB")
            data = json.loads(raw)
            rates = estimate(data, pricing_key(profile), 1)
            return json.dumps(
                dict(
                    version=1,
                    chapters=10,
                    pricing_key=pricing_key(profile),
                    currency=data["currency"],
                    per_codepoint=rates,
                )
            )

        def save(sample):
            self.prefs.setValue("costs/" + profile.name, sample)
            self.note.setText("十章费用样本已保存，整书标注前将显示费用区间")

        self.run("读取费用样本", read_sample, save)

    def confirm_book_analysis(self, view, size):
        chapters = tuple(self.chapters)
        p = self.profiles.all()[self.llm_name]
        try:
            sample = json.loads(self.prefs.value("costs/" + p.name, "{}"))
            low, high = estimate(sample, pricing_key(p), size)
            forecast = f"按十章样本外推，{size} 个原文码点约 {low:.4g}–{high:.4g} {p.currency}。"
        except (ValueError, TypeError):
            forecast = "费用待估，可先导入相同定价配置的十章样本。"
        caps = self.provider.capabilities()
        title = next(name for key, name in self.books if key == view.book)
        if (
            QMessageBox.question(
                self,
                "整书云端分析",
                f"将《{title}》的 {len(chapters)} 章正文和角色表逐块发送到 {p.endpoint}。"
                f"本次共用 {p.budget} 次请求预算（含重试）。{forecast}是否开始整书标注？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.stop()

        def analyze(event):
            remote = RemoteLLM(
                p,
                self.profiles.credential(p),
                record=lambda r: self.repo.call("record_attempt", view.book, r),
            )
            count = 0
            for chapter, _ in chapters:
                self.repo.call("guard", view.book, view.revision + count)
                current = self.repo.call("view", chapter, 0, caps)
                count += analyze_chapter(self.repo, current, caps, remote, event)
            return count

        def done(_):
            self.load_page(view.page, view.offset)

        self.run(
            "整书云端标注", analyze, done, guard=lambda: self.view is view, task=True
        )

    def show_attempts(self):
        def show(rows):
            dialog = QDialog(self)
            dialog.setWindowTitle("最近 100 次供应商尝试")
            dialog.resize(760, 440)
            layout = QVBoxLayout(dialog)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                or "尚无供应商请求"
            )
            layout.addWidget(text)
            dialog.exec()

        self.run("读取请求记录", lambda _: self.repo.call("attempts"), show)
