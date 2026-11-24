"""Casting clues in Chinese fiction and the Qwen3-TTS CustomVoice presets."""

import re

# Preset order gives voices native to Chinese priority within each group.
QWEN_PRESETS = {
    "vivian": ("female", "明亮女声", "中文"),
    "serena": ("female", "温柔女声", "中文"),
    "uncle_fu": ("male", "醇厚成熟男声", "中文"),
    "dylan": ("male", "清朗年轻男声", "北京话"),
    "eric": ("male", "活泼男声", "四川话"),
    "ono_anna": ("female", "俏皮女声", "日语"),
    "sohee": ("female", "温暖女声", "韩语"),
    "ryan": ("male", "富有节奏的男声", "英语"),
    "aiden": ("male", "阳光男声", "美式英语"),
}


# Built-in Mandarin presets for Edge TTS (ShortName -> (gender, display_name, region_label)).
EDGE_PRESETS = {
    "zh-CN-XiaoxiaoNeural": ("female", "晓晓", "普通话"),
    "zh-CN-XiaoyiNeural": ("female", "晓伊", "普通话"),
    "zh-CN-YunxiNeural": ("male", "云希", "普通话"),
    "zh-CN-YunjianNeural": ("male", "云健", "普通话"),
}

EDGE_KNOWN_VOICES = {
    **EDGE_PRESETS,
    "zh-CN-YunxiaNeural": ("male", "云夏", "普通话"),
    "zh-CN-YunyangNeural": ("male", "云扬", "普通话"),
    "zh-CN-liaoning-XiaobeiNeural": ("female", "晓北", "东北话"),
    "zh-CN-shaanxi-XiaoniNeural": ("female", "晓妮", "陕西话"),
    "zh-HK-HiuGaaiNeural": ("female", "晓佳", "粤语"),
    "zh-HK-HiuMaanNeural": ("female", "晓曼", "粤语"),
    "zh-HK-WanLungNeural": ("male", "云龙", "粤语"),
    "zh-TW-HsiaoChenNeural": ("female", "晓臻", "台湾普通话"),
    "zh-TW-HsiaoYuNeural": ("female", "晓雨", "台湾普通话"),
    "zh-TW-YunJheNeural": ("male", "云哲", "台湾普通话"),
}

EDGE_DEFAULT_NARRATOR = "zh-CN-XiaoxiaoNeural"

EDGE_BUILTIN_CATALOG = tuple(
    {
        "short_name": short_name,
        "locale": "zh-CN",
        "gender": "Female" if kind == "female" else "Male",
        "friendly_name": f"{name}（{region}）",
    }
    for short_name, (kind, name, region) in EDGE_PRESETS.items()
)

_EDGE_KNOWN_LOWER = {k.lower(): (k, v) for k, v in EDGE_KNOWN_VOICES.items()}
_EDGE_PRESETS_LOWER = {k.lower(): (k, v) for k, v in EDGE_PRESETS.items()}


def _gender_label(gender):
    g = (gender or "").strip().lower()
    if g == "female":
        return "女声"
    if g == "male":
        return "男声"
    return ""


def voice_label(provider, voice, meta=None):
    if provider == "local_qwen":
        preset = QWEN_PRESETS.get(voice.lower())
        return f"{voice} · {preset[1]}（{preset[2]}）" if preset else voice
    if provider == "edge_tts":
        known = _EDGE_KNOWN_LOWER.get(voice.lower())
        if known:
            _, (kind, name, region) = known
            return f"{name} · {_gender_label(kind)}（{region}） [{voice}]"
        if meta:
            g = _gender_label(meta.get("gender"))
            loc = (meta.get("locale") or "").strip()
            parts = [p for p in (g, loc) if p]
            if parts:
                return f"{voice} · {' / '.join(parts)}"
        return voice
    return voice


def matching_voices(voice_type, voices, provider="local_qwen", voice_genders=None):
    available = {voice.lower(): voice for voice in voices}
    if provider == "edge_tts":
        seen = set()
        result = []
        # 1. Built-in Mandarin presets first.
        for key, (_, (kind, _, _)) in _EDGE_PRESETS_LOWER.items():
            if kind == voice_type and key in available and key not in seen:
                result.append(available[key])
                seen.add(key)
        # 2. Other known Chinese voices next.
        for key, (_, (kind, _, _)) in _EDGE_KNOWN_LOWER.items():
            if kind == voice_type and key in available and key not in seen:
                result.append(available[key])
                seen.add(key)
        # 3. Any other catalog voices with matching gender metadata.
        if voice_genders:
            genders_lower = {k.lower(): (v or "").lower() for k, v in voice_genders.items()}
            for voice in voices:
                key = voice.lower()
                if key not in seen and genders_lower.get(key) == voice_type:
                    result.append(voice)
                    seen.add(key)
        return result
    return [
        available[key]
        for key, (kind, _, _) in QWEN_PRESETS.items()
        if kind == voice_type and key in available
    ]


_FEMALE = (
    "女主角|女主|女配角|女角色|女孩子|小女孩|女孩|女童|少女|女生|女性|女子|女人|妇人|"
    "姑娘|小姐|女士|公主|王妃|夫人|女儿|母亲|妈妈|姐姐|妹妹|师姐|师妹|奶奶|婆婆"
)
_MALE = (
    "男主角|男主|男配角|男角色|男孩子|小男孩|男孩|男童|少年郎|男生|男性|男子|男人|"
    "公子|少爷|世子殿下|世子|王子|儿子|父亲|爸爸|哥哥|弟弟|师兄|师弟|爷爷"
)
_TITLE = rf"(?:{_FEMALE}|{_MALE})"
_TITLE_END = re.compile(rf"({_TITLE})$")
_VOICE_TAG = re.compile(r"[（(]\s*(女|男)(?:性|声)?\s*[)）]$")
_IDENTITY_AFTER = re.compile(rf"^(?:是|是个|是位|是一名|是一个|是一位)({_TITLE})(?=[，。！？；：、\s]|$)")
_NAMING = re.compile(r"(?:名叫|名为|名唤|叫作|绰号|外号)[“「]?$")
_NAMED_PERSON = re.compile(rf"^[”」]?的(?:年幼|亲生|独生|宝贝|小|傻)?({_TITLE})")
_SUBJECT = rf"(?P<subject>她|他|(?:这|那)?(?:位|名|个)?{_TITLE})"
_VERB = r"(?:低声|轻声|小声|大声|高声|轻轻地?|缓缓地?|笑着|冷冷地?)?(?:说道|说着|问道|答道|喊道|叫道|笑道|回答|说|问|答|道|喊)"
_BEFORE_SPEECH = re.compile(rf"(?:^|[。！？；，：“”」』\s]){_SUBJECT}{_VERB}[：:，,]\s*$")
_AFTER_SPEECH = re.compile(rf"^\s*{_SUBJECT}{_VERB}(?=[。！？；]|$)")
_STYLE_VOICE = re.compile(r"女声|男声|\b(?:female|male)(?: voice)?\b", re.I)


def _voice_type(subject):
    return "female" if subject == "她" or re.search(rf"(?:{_FEMALE})$", subject) else "male"


def identity_voice_type(name, before="", after=""):
    # Only inspect a title in the character's own name or directly next to its
    # recorded name anchor, e.g. “少女阿宁” or “阿宁是一位姑娘。”.
    tag = _VOICE_TAG.search(name)
    if tag:
        return "female" if tag[1] == "女" else "male"
    match = _TITLE_END.search(name) or _TITLE_END.search(before) or _IDENTITY_AFTER.match(after)
    if not match and _NAMING.search(before):
        match = _NAMED_PERSON.match(after)
    return _voice_type(match[1]) if match else None


def dialogue_voice_types(before, after, style):
    kinds = set()
    for pattern, context in (
        (_BEFORE_SPEECH, before.rstrip('“‘「『" \t')),
        (_AFTER_SPEECH, after.lstrip('”’」』" \t')),
    ):
        match = pattern.search(context)
        if match:
            kinds.add(_voice_type(match["subject"]))
    if style and not re.search(r"模仿|变声|假装|不要|避免|不用|勿用|\b(?:not|imitate)\b", style, re.I):
        for match in _STYLE_VOICE.finditer(style):
            kinds.add("female" if match[0].lower().startswith(("女", "female")) else "male")
    return kinds
