"""Extrapolate the ten-chapter attempt ledger and save a reusable desktop cost sample."""

import argparse
import json
from pathlib import Path

from novel_tts.application.costs import calibrate, estimate


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument(
        "measurements", type=Path, help="evaluate_llm.py --output 的 JSONL"
    )
    cli.add_argument("--total-characters", type=int, required=True)
    cli.add_argument("--save-calibration", type=Path, help="桌面可导入的十章费用报告")
    args = cli.parse_args()
    corpus = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures/regression.json").read_text(
            encoding="utf-8"
        )
    )
    try:
        with args.measurements.open(encoding="utf-8") as f:
            report = calibrate(corpus, [json.loads(line) for line in f])
        bounds = estimate(report, report["pricing_key"], args.total_characters)
    except (ValueError, TypeError, KeyError) as exc:
        print(
            json.dumps(
                {"estimate": None, "status": f"费用待估 · {exc}"}, ensure_ascii=False
            )
        )
        return 2
    if args.save_calibration:
        with args.save_calibration.open("x", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            report
            | {
                "estimated_range": bounds,
                "basis": "十章每码点费用的实测最小值及最大值",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
