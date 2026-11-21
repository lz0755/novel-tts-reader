from dataclasses import replace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,QComboBox,QDialog,QDialogButtonBox,QFormLayout,QInputDialog,QLabel,
    QLineEdit,QMessageBox,QPlainTextEdit,QSpinBox,QVBoxLayout,
)

from novel_tts.application.analysis import analyze_chapter
from novel_tts.infrastructure.profiles import Profiles
from novel_tts.providers.http import RequestBudget
from novel_tts.providers.llm import RemoteLLM


class CloudSettings:
    def init_cloud(self):
        self.profiles = Profiles(self.paths.data)
        self.prefs = QSettings(str(self.paths.data/"preferences.ini"),QSettings.Format.IniFormat)
        self.llm_name = self.prefs.value("llm","openai")
        menu = self.menuBar().addMenu("供应商")
        menu.addAction("配置小说分析服务",self.configure_llm)
        menu.addAction("云端标注本章…",self.analyze_cloud)
        menu.addAction("请求与费用记录",self.show_attempts)
        self.provider_menu = menu

    def configure_llm(self):
        choices = ["openai","deepseek","custom"]
        index = choices.index(self.llm_name) if self.llm_name in choices else 0
        name,ok = QInputDialog.getItem(self,"小说分析","选择配置",choices,index,False)
        if ok:
            self.configure_profile(name)

    def configure_profile(self,name):
        profiles = self.profiles.all()
        p = profiles[name]
        endpoint,model = QLineEdit(p.endpoint),QLineEdit(p.model)
        protocol = QComboBox()
        protocol.addItems(["qwen_audio_http"] if name=="qwen_cloud" else ["responses","chat_completions"])
        protocol.setCurrentText(p.protocol)
        output = QComboBox()
        output.addItems(["json_schema","json_object","text"])
        output.setCurrentText(p.output)
        voices = QLineEdit(", ".join(p.voices))
        budget = QSpinBox()
        budget.setRange(1,10000)
        budget.setValue(p.budget)
        secret = QLineEdit()
        secret.setEchoMode(QLineEdit.EchoMode.Password)
        secret.setPlaceholderText("留空以使用现有凭据")
        remember = QCheckBox("将新密钥保存到系统凭据服务")
        fields = [("服务端点",endpoint),("模型 ID",model),("协议",protocol),
                  ("音色 ID（逗号分隔）",voices) if name=="qwen_cloud" else ("模型输出能力",output),
                  ("本次操作请求上限（含重试）",budget),("API 密钥",secret),("",remember)]
        if not self.edit_form("配置 "+name,fields):
            return
        updated = replace(p,endpoint=endpoint.text().strip(),model=model.text().strip(),protocol=protocol.currentText(),
                          output=output.currentText(),voices=tuple(v.strip() for v in voices.text().split(",") if v.strip()),budget=budget.value())
        key,persist = secret.text(),remember.isChecked()
        def saved(message):
            if name!="qwen_cloud":
                self.llm_name = name
                self.prefs.setValue("llm",name)
            self.note.setText(message)
        self.run("保存供应商配置",lambda _:self.profiles.save(updated,key,persist),saved)

    def analyze_cloud(self):
        if not self.view:
            return
        view,caps = self.view,self.provider.capabilities()
        p = self.profiles.all()[self.llm_name]
        if QMessageBox.question(self,"允许发送本章正文",
                f"将《{view.title}》的正文、最多 256 码点前文及本书角色名发送到 {p.endpoint}。\n"
                f"模型 {p.model}，用于标注角色和台词。最多 {p.budget} 次请求（含重试），费用待估。是否继续？"
                )!=QMessageBox.StandardButton.Yes:
            return
        self.stop()
        def analysis(event):
            remote = RemoteLLM(p,self.profiles.credential(p),record=lambda row:self.repo.call("record_attempt",view.book,row))
            return analyze_chapter(self.repo,view,caps,remote,event)
        def complete(n):
            self.note.setText(f"已验证并提交 {n} 批标注")
            self.load_page(view.page,view.offset)
        def interrupted(exc):
            self.note.setText(f"云端标注中止 · {exc}")
            self.load_page(view.page,view.offset)
        self.run("分析本章",analysis,complete,guard=lambda:self.view is view,failed=interrupted,task=True)

    def show_attempts(self):
        import json
        def show(rows):
            dialog = QDialog(self)
            dialog.setWindowTitle("最近 100 次供应商尝试")
            dialog.resize(760,440)
            layout = QVBoxLayout(dialog)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText("\n".join(json.dumps(r,ensure_ascii=False) for r in rows) or "尚无供应商请求")
            layout.addWidget(text)
            dialog.exec()
        self.run("读取请求记录",lambda _:self.repo.call("attempts"),show)
