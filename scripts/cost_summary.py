"""Extrapolate from the actual, dated ten-chapter attempt ledger; unknown stays unknown."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument(
        "measurements", type=Path, help="evaluate_llm.py --output 的 JSONL"
    )
    cli.add_argument("--total-characters", type=int, required=True)
    args = cli.parse_args()
    corpus = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures/regression.json").read_text(
            encoding="utf-8"
        )
    )
    nchar = {
        c["title"]: sum(len(p["text"]) for p in c["parents"])
        for c in corpus["chapters"]
    }
    records = [
        json.loads(s)
        for s in args.measurements.read_text(encoding="utf-8").splitlines()
    ]
    rows = {r["attempt"]: r for r in records if r.get("kind") == "usage"}
    completed = {r["id"] for r in records if r.get("parts")}
    required = {p["id"] for c in corpus["chapters"] for p in c["parents"]}
    costs = defaultdict(float)
    currency = {r["currency"] for r in rows.values()}
    if (
        completed != required
        or not rows
        or len(currency) != 1
        or any(r["cost"] is None or not r["price_date"] for r in rows.values())
    ):
        print(
            json.dumps(
                {
                    "estimate": None,
                    "status": "费用待估：需十章完整样本、同币种日期价格及每次尝试的费用",
                },
                ensure_ascii=False,
            )
        )
        return 2
    for row in rows.values():
        costs[row["chapter"]] += row["cost"]
    rates = [costs[ch] / n for ch, n in nchar.items()]
    print(
        json.dumps(
            dict(
                currency=currency.pop(),
                attempts=len(rows),
                sample_cost=sum(costs.values()),
                estimated_range=[
                    min(rates) * args.total_characters,
                    max(rates) * args.total_characters,
                ],
                basis="十章每码点费用的实际最小值及最大值",
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
