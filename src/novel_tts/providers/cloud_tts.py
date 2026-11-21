import audioop
import base64
import io
import threading
import time
import wave
from urllib.parse import urlsplit

import httpx

from novel_tts.domain.models import Cancelled, Capabilities, ProviderError, SynthesisRequest, fingerprint
from novel_tts.infrastructure.audio import RATE, inspect_wav
from novel_tts.providers.http import Remote

MAX_BYTES = 32*1024*1024


def normalize(data,path):
    if not data or len(data)>MAX_BYTES:
        raise ValueError("音频为空或超过单段限制")
    with wave.open(io.BytesIO(data),"rb") as wav:
        channels,width,rate,frames = wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes()
        if channels not in (1,2) or width not in (1,2,3,4) or not 8000<=rate<=192000 or not 0<frames<=rate*600:
            raise ValueError("不支持的 PCM 参数")
        if frames*width*channels>MAX_BYTES:
            raise ValueError("音频超过单段限制")
        pcm = wav.readframes(frames)
        if len(pcm)!=frames*width*channels:
            raise ValueError("下载音频被截断")
    if width==1:
        pcm=audioop.bias(pcm,1,-128)
    if width!=2:
        pcm=audioop.lin2lin(pcm,width,2)
    if channels==2:
        pcm=audioop.tomono(pcm,2,.5,.5)
    if rate!=RATE:
        pcm,_=audioop.ratecv(pcm,2,1,rate,RATE,None)
    with wave.open(str(path),"wb") as out:
        out.setparams((1,2,RATE,0,"NONE","not compressed"))
        out.writeframes(pcm)


class QwenCloudTTS(Remote):
    def __init__(self,*args,allowed=True,**kwargs):
        super().__init__(*args,**kwargs)
        self.allowed = threading.Event()
        if allowed:
            self.allowed.set()
        self.links = {}
        self.lock = threading.Lock()

    def capabilities(self):
        p=self.profile
        return Capabilities(p.name,p.model,p.protocol,p.revision or "alias",p.voices,p.instruct,input_limit=600)

    def prepare(self,text,voice,style=""):
        p=self.profile
        if voice not in p.voices:
            raise ValueError("未配置此音色")
        return SynthesisRequest(text,p.name,p.model,p.revision or "alias",voice,"cloud",style if p.instruct else "",
                                parameters=(("endpoint",fingerprint(p.endpoint)),("protocol",p.protocol)))

    def download(self,url,cancel):
        parts=urlsplit(url)
        host=parts.hostname or ""
        endpoint=urlsplit(self.profile.endpoint)
        same=host==endpoint.hostname and parts.scheme==endpoint.scheme and parts.port==endpoint.port
        if (not same and not (parts.scheme=="https" and host.endswith(".aliyuncs.com"))) or parts.username or parts.password:
            raise ProviderError("音频下载地址不属于当前服务或受支持的阿里云对象存储")
        # Signed media URLs have their own credentials. Never forward the API bearer.
        with httpx.Client(transport=self.transport,timeout=15,follow_redirects=False) as client:
            for n in range(2):
                if cancel.is_set():
                    raise Cancelled()
                try:
                    with client.stream("GET",url) as r:
                        if r.status_code in (401,403,404,410):
                            raise ProviderError("音频下载链接失效，需重新授权生成")
                        if not r.is_success:
                            raise httpx.ReadError("音频下载失败")
                        data = bytearray()
                        for block in r.iter_bytes():
                            if cancel.is_set():
                                raise Cancelled()
                            data.extend(block)
                            if len(data)>MAX_BYTES:
                                raise ProviderError("音频下载超过大小上限")
                        return bytes(data)
                except httpx.RequestError:
                    if n or cancel.wait(.15):
                        raise ProviderError("音频下载失败；保留已有链接供重试",True) from None

    def synthesize(self,request,destination,cancel):
        if not self.allowed.is_set():
            raise ProviderError("本章尚未允许上传，缓存未命中")
        if len(request.text)>self.capabilities().input_limit:
            raise ProviderError("单段超过当前音频配置长度上限")
        if "YOUR_WORKSPACE_ID" in self.profile.endpoint.upper():
            raise ProviderError("请先填写地域匹配的实际工作区端点")
        with self.lock:
            link=self.links.get(request.key)
        if link and link[1] and link[1]<=time.time():
            with self.lock:
                self.links.pop(request.key,None)
            raise ProviderError("原音频链接已过期，本次没有重新提交合成")
        if not link:
            args=dict(text=request.text,voice=request.voice,format="wav",sample_rate=RATE)
            if request.style:
                args["instruction"]=request.style
            response=self.post(self.profile.endpoint,{"model":request.model,"input":args},cancel)
            if response.headers.get("content-type","").split(";")[0] in ("audio/wav","audio/x-wav"):
                data=response.content
            else:
                try:
                    raw=response.json()
                    out=raw["output"]
                    if out.get("finish_reason")!="stop":
                        raise ProviderError("音频响应未正常结束")
                    audio=out["audio"]
                    if audio.get("data"):
                        data=base64.b64decode(audio["data"],validate=True)
                    elif audio.get("url"):
                        link=(audio["url"],audio.get("expires_at"))
                        with self.lock:
                            self.links[request.key]=link
                    else:
                        raise ProviderError("未返回音频")
                except (KeyError,TypeError,ValueError):
                    raise ProviderError("音频响应格式无效") from None
        if link:
            data=self.download(link[0],cancel)
        if cancel.is_set():
            raise Cancelled()
        normalize(data,destination)
        artifact=inspect_wav(destination,request)
        with self.lock:
            self.links.pop(request.key,None)
        return artifact
