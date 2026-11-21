"""Ten-chapter cost calibration shared by the desktop preview and CLI."""

import math

from novel_tts.domain.models import fingerprint


def pricing_key(profile):
    return fingerprint(
        [
            profile.name,
            profile.endpoint,
            profile.protocol,
            profile.model,
            profile.revision,
            profile.price_date,
            profile.currency,
            profile.unit,
            profile.input_per_million,
            profile.output_per_million,
        ]
    )


def calibrate(corpus, records):
    chapters = {
        ch["title"]: sum(len(p["text"]) for p in ch["parents"])
        for ch in corpus["chapters"]
    }
    expected = {p["id"] for ch in corpus["chapters"] for p in ch["parents"]}
    returned = {r["id"] for r in records if r.get("parts")}
    attempts = {r["attempt"]: r for r in records if r.get("kind") == "usage"}
    charges = list(attempts.values())
    if len(chapters) != 10 or expected != returned or not charges:
        raise ValueError("需要十章完整的标注与费用记录")
    keys = {r.get("pricing_key") for r in charges}
    currencies = {r.get("currency") for r in charges}
    if len(keys) != 1 or not all(keys) or len(currencies) != 1 or not all(currencies):
        raise ValueError("十章记录须使用相同的供应商、模型和定价配置")
    if {r.get("chapter") for r in charges} != set(chapters):
        raise ValueError("费用记录未完整覆盖十章")
    costs = dict.fromkeys(chapters, 0.0)
    for row in charges:
        amount = row.get("cost")
        if (
            not row.get("price_date")
            or type(amount) not in (int, float)
            or not math.isfinite(amount)
            or amount < 0
        ):
            raise ValueError("存在未知费用或缺少价格日期的尝试")
        costs[row["chapter"]] += amount
    rates = [costs[ch] / n for ch, n in chapters.items()]
    return dict(
        version=1,
        chapters=10,
        pricing_key=keys.pop(),
        currency=currencies.pop(),
        attempts=len(attempts),
        sample_cost=sum(costs.values()),
        per_codepoint=[min(rates), max(rates)],
    )


def estimate(report, key, characters):
    if (
        not isinstance(report, dict)
        or report.get("version") != 1
        or report.get("chapters") != 10
    ):
        raise ValueError("需要十章费用报告 v1")
    if (
        report.get("pricing_key") != key
        or not isinstance(report.get("currency"), str)
        or not report["currency"]
    ):
        raise ValueError("费用样本与当前端点、模型或价格配置不符")
    rates = report.get("per_codepoint")
    if not isinstance(rates, (list, tuple)) or len(rates) != 2 or characters < 0:
        raise ValueError("费用区间无效")
    if (
        not all(type(r) in (int, float) and math.isfinite(r) for r in rates)
        or not 0 <= rates[0] <= rates[1]
    ):
        raise ValueError("样本价格无效")
    return [rate * characters for rate in rates]
