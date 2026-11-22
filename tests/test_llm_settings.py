import keyring
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialogButtonBox, QLabel

from novel_tts.infrastructure.paths import Paths
from novel_tts.ui.window import ReaderWindow
from test_mock_flow import wait


@pytest.mark.parametrize(
    "name,remember", [("openai", True), ("deepseek", False), ("custom", True)]
)
def test_save_and_reopen_analysis_profile(app, tmp_path, monkeypatch, name, remember):
    endpoint, model, key = "https://example.test/v1", "reader-model", "fixture-only-key"
    vault = {}

    def store_key(service, ref, value):
        vault[service, ref] = value

    monkeypatch.setattr(keyring, "set_password", store_key)
    monkeypatch.setattr(
        keyring, "get_password", lambda service, ref: vault.get((service, ref))
    )

    def select_profile():
        picker = app.activeModalWidget()
        picker.findChild(QComboBox).setCurrentText(name)
        QTimer.singleShot(0, fill_profile)
        picker.accept()

    def fill_profile():
        dialog = app.activeModalWidget()
        try:
            fields = {
                label.text(): label.buddy() for label in dialog.findChildren(QLabel)
            }
            fields["服务端点"].setText(endpoint)
            fields["模型 ID"].setText(model)
            fields["API 密钥"].setText(key)
            dialog.findChild(QCheckBox).setChecked(remember)
        finally:
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Save).click()

    paths = Paths(tmp_path)
    window = ReaderWindow(paths)
    try:
        QTimer.singleShot(0, select_profile)
        window.configure_llm()
        wait(app, lambda: not window.callbacks)
        assert window.llm_name == name, window.note.text()
        assert window.profiles.credential(window.profiles.all()[name]) == key
        assert ("系统凭据服务" if remember else "仅本次会话") in window.note.text()
        assert key not in window.profiles.path.read_text(encoding="utf-8")
    finally:
        window.close()

    reopened = ReaderWindow(paths)
    try:
        assert reopened.llm_name == name
        profile = reopened.profiles.all()[name]
        assert (profile.endpoint, profile.model) == (endpoint, model)
        if remember:
            assert reopened.profiles.credential(profile) == key
        else:
            assert not vault
    finally:
        reopened.close()
