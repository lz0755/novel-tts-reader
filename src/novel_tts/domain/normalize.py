"""Conservative spoken forms. Source text is always retained for display and anchors."""

import re

DIGITS = "零一二三四五六七八九"


def small_number(s):
    if len(s) > 4 or (len(s) > 1 and s.startswith("0")):
        return "".join(DIGITS[int(c)] for c in s)
    n = int(s)
    if n == 0:
        return "零"
    out = []
    zero = False
    for unit, word in [(1000, "千"), (100, "百"), (10, "十"), (1, "")]:
        digit, n = divmod(n, unit)
        if digit:
            if zero:
                out.append("零")
            if digit != 1 or unit != 10 or out:
                out.append(DIGITS[digit])
            out.append(word)
            zero = False
        elif out and n:
            zero = True
    return "".join(out)


def spoken_form(text):
    def number(m):
        value = m[0]
        if (
            m.end() < len(text)
            and text[m.end()] == "年"
            and len(value) == 4
            and "." not in value
        ):
            return "".join(DIGITS[int(c)] for c in value)
        first, dot, fraction = value.partition(".")
        return small_number(first) + (
            "点" + "".join(DIGITS[int(c)] for c in fraction) if dot else ""
        )

    return re.sub(r"(?<![A-Za-z0-9_])[0-9]+(?:\.[0-9]+)?(?![A-Za-z0-9_])", number, text)
