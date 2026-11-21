import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

import keyring


@dataclass(frozen=True)
class Profile:
    name: str
    endpoint: str
    model: str
    protocol: str
    output: str = "json_object"
    env: str = ""
    budget: int = 16
    context_bytes: int = 18000
    voices: tuple[str,...] = ()
    instruct: bool = False
    revision: str = ""
    price_date: str = ""
    input_per_million: float | None = None
    output_per_million: float | None = None
    currency: str = ""
    unit: str = "tokens"

    def validate(self):
        u = urlsplit(self.endpoint)
        if ((u.scheme!="https" and not (u.scheme=="http" and u.hostname in ("127.0.0.1","localhost","::1")))
            or not u.hostname or u.username or u.password or u.query or u.fragment):
            raise ValueError("端点须使用 HTTPS 或本机 HTTP，不应包含凭据或查询参数")
        if not self.model.strip() or not 1<=self.budget<=10000 or self.context_bytes<4096:
            raise ValueError("请核对模型、请求上限和上下文预算")
        if self.protocol not in ("chat_completions","responses","qwen_audio_http"):
            raise ValueError("未知协议")
        if self.output not in ("json_schema","json_object","text"):
            raise ValueError("未知结构化输出能力")
        if self.protocol=="qwen_audio_http" and (not self.voices or not all(self.voices)):
            raise ValueError("请填写实际音色 ID")
        return self

    @property
    def credential_ref(self):
        return f"{self.name}@{urlsplit(self.endpoint).netloc}"


DEFAULTS = {
    "openai":Profile("openai","https://api.openai.com/v1","gpt-4.1-mini","responses","json_schema","OPENAI_API_KEY"),
    "deepseek":Profile("deepseek","https://api.deepseek.com/v1","deepseek-flash","chat_completions","json_object","DEEPSEEK_API_KEY"),
    "custom":Profile("custom","http://127.0.0.1:8080/v1","custom-model","chat_completions","text"),
    "qwen_cloud":Profile("qwen_cloud","https://YOUR_WORKSPACE_ID.ap-southeast-1.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
                         "qwen-audio-3.1-tts-flash","qwen_audio_http",env="DASHSCOPE_API_KEY",
                         voices=("longanhuan_v3.1",),unit="characters"),
}


class Profiles:
    def __init__(self,root):
        self.path = Path(root)/"profiles.json"
        self.session = {}
        self.lock = RLock()

    def all(self):
        values = DEFAULTS.copy()
        if self.path.is_file():
            for name,raw in json.loads(self.path.read_text(encoding="utf-8")).items():
                if name not in DEFAULTS:
                    continue
                values[name] = replace(DEFAULTS[name],**(raw|{"voices":tuple(raw.get("voices",()))})).validate()
        return values

    def save(self,profile,secret="",remember=False):
        profile.validate()
        with self.lock:
            values = self.all()
            values[profile.name] = profile
            data = {name:asdict(p) for name,p in values.items()}
            fd,temporary = tempfile.mkstemp(dir=self.path.parent,suffix=".tmp")
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as f:
                    json.dump(data,f,indent=2,ensure_ascii=False)
                os.replace(temporary,self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            if secret:
                self.session[profile.credential_ref]=secret
                if remember:
                    try:
                        keyring.set_password("NovelTTSReader",profile.credential_ref,secret)
                        return "配置已保存，密钥保存在系统凭据服务"
                    except keyring.errors.KeyringError:
                        return "系统凭据服务不可用，密钥仅本次会话有效"
            return "配置已保存"

    def credential(self,profile):
        value = self.session.get(profile.credential_ref)
        if not value:
            try:
                value = keyring.get_password("NovelTTSReader",profile.credential_ref)
            except keyring.errors.KeyringError:
                pass
        if not value and profile.env:
            host = urlsplit(profile.endpoint).hostname or ""
            default = DEFAULTS[profile.name]
            allowed = host==urlsplit(default.endpoint).hostname
            if profile.name=="qwen_cloud":
                allowed = host.endswith((".ap-southeast-1.maas.aliyuncs.com",".cn-beijing.maas.aliyuncs.com"))
            if allowed and profile.env==default.env:
                value = os.environ.get(profile.env)
        if not value and profile.name!="custom":
            raise ValueError("未配置此端点的 API 密钥")
        return value or ""
