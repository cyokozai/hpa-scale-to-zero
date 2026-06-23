#!/usr/bin/env python3
"""
HPA Scale to Zero 計測結果の統計集計。

各 run について以下の 2 つを計算:
  - Scale from Zero: triggers.csv の "push 50" 時刻 →
                     measurement.csv で 最初に replicas>=1 になった時刻 までの秒数
  - Scale to Zero:   triggers.csv の "push 0"  時刻 →
                     measurement.csv で 最初に replicas==0 になった時刻 までの秒数

n run 分を集計して avg/σ/median/min/max を出力。
依存: Python 標準ライブラリのみ。

使い方:
  python3 scripts/aggregate.py <batch_dir>
  例: python3 scripts/aggregate.py /tmp/measure-batch
"""

import csv
import statistics
import sys
from datetime import datetime
from pathlib import Path


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def analyze_run(rundir: Path) -> dict | None:
    csv_file = rundir / "measurement.csv"
    triggers_file = rundir / "triggers.csv"
    if not csv_file.exists() or not triggers_file.exists():
        return None

    # triggers.csv → push 50 / push 0 のタイムスタンプを取得
    push_50 = push_0 = None
    with open(triggers_file) as f:
        for row in csv.DictReader(f):
            ts = parse_ts(row["timestamp"])
            if row["action"] == "push 50":
                push_50 = ts
            elif row["action"] == "push 0":
                push_0 = ts
    if not push_50 or not push_0:
        return None

    # measurement.csv → 最初の replicas 遷移時刻を探す
    sfz_ts = stz_ts = None
    with open(csv_file) as f:
        for row in csv.DictReader(f):
            ts = parse_ts(row["timestamp"])
            rep = int(row["replicas"])
            if sfz_ts is None and ts >= push_50 and rep >= 1:
                sfz_ts = ts
            if stz_ts is None and ts >= push_0 and rep == 0:
                stz_ts = ts

    return {
        "run_id": rundir.name,
        "sfz": (sfz_ts - push_50).total_seconds() if sfz_ts else None,
        "stz": (stz_ts - push_0).total_seconds() if stz_ts else None,
    }


def fmt_stats(values: list, unit: str = "s") -> str:
    xs = [v for v in values if v is not None]
    if not xs:
        return "n/a"
    n = len(xs)
    avg = statistics.mean(xs)
    sd = statistics.stdev(xs) if n >= 2 else 0
    med = statistics.median(xs)
    return f"n={n}, mean={avg:.1f}{unit}, σ={sd:.2f}, median={med:.1f}, min={min(xs):.1f}, max={max(xs):.1f}"


def main():
    if len(sys.argv) < 2:
        print("Usage: aggregate.py <batch_dir>", file=sys.stderr)
        sys.exit(2)

    batch_dir = Path(sys.argv[1])
    run_dirs = sorted(p for p in batch_dir.iterdir() if p.is_dir() and p.name.startswith("run-"))
    results = [r for r in (analyze_run(rd) for rd in run_dirs) if r]

    if not results:
        print("No valid runs found", file=sys.stderr)
        sys.exit(2)

    lines = [
        f"# HPA Scale to Zero 計測サマリ ({len(results)} runs)",
        "",
        "## 統計",
        "",
        f"- Scale from Zero: {fmt_stats([r['sfz'] for r in results])}",
        f"- Scale to Zero:   {fmt_stats([r['stz'] for r in results])}",
        "",
        "## 各 run の値",
        "",
        "| run | Scale from Zero (s) | Scale to Zero (s) |",
        "|---|---:|---:|",
    ]
    for r in results:
        sfz = f"{r['sfz']:.1f}" if r["sfz"] is not None else "n/a"
        stz = f"{r['stz']:.1f}" if r["stz"] is not None else "n/a"
        lines.append(f"| {r['run_id']} | {sfz} | {stz} |")

    out_file = batch_dir / "stats.md"
    out_file.write_text("\n".join(lines) + "\n")
    print(f"Saved: {out_file}\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
