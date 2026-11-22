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


def voice_label(provider, voice):
    preset = QWEN_PRESETS.get(voice.lower()) if provider == "local_qwen" else None
    return f"{voice} · {preset[1]}（{preset[2]}）" if preset else voice


def matching_voices(voice_type, voices):
    available = {voice.lower(): voice for voice in voices}
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
