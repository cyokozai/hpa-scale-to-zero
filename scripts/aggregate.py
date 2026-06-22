#!/usr/bin/env python3
"""
LT Pushgateway 構成専用の統計集計。
aggregate.py の Scale to Zero 定義 (scale-up → scale-down) は
Pushgateway 構成では人工 hold 時間 (push_zero_at) が混入するので、
push 0 のタイムスタンプを起点にし直す。

Usage:
  python3 scripts/lt-aggregate.py <batch_dir>
  例: python3 scripts/lt-aggregate.py docs/verification-lt/batch-n5-20260618-072042
"""
import csv
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def total_replicas(s: str) -> int:
    try:
        return int(s.split("/")[1])
    except (IndexError, ValueError):
        return 0


def analyze_run(rundir: Path) -> dict:
    csv_file = rundir / "hpa.csv"
    log_file = rundir / "hpa-run.log"
    events_file = rundir / "hpa-events.jsonl"

    if not csv_file.exists():
        return {"run_id": rundir.name, "error": "csv missing"}

    # sync_ts = 最初の CSV 行 = push 50 完了直後
    with open(csv_file) as f:
        reader = csv.DictReader(f)
        first_row = next(reader, None)
        if not first_row:
            return {"run_id": rundir.name, "error": "csv empty"}
        sync_ts = parse_ts(first_row["timestamp"])

    # push 0 タイムスタンプを run.log から取得
    push_0_ts = None
    if log_file.exists():
        with open(log_file) as f:
            for line in f:
                m = re.search(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+pushed queue_length=0", line)
                if m:
                    push_0_ts = parse_ts(m.group(1))
                    break

    # CSV: 最初に replicas>=1 観測した時刻 (sampling 5s)
    sfz_pod_ts = None
    stz_pod_ts = None
    with open(csv_file) as f:
        for row in csv.DictReader(f):
            ts = parse_ts(row["timestamp"])
            tot = total_replicas(row["replicas"])
            if sfz_pod_ts is None and tot >= 1:
                sfz_pod_ts = ts
            if sfz_pod_ts is not None and stz_pod_ts is None and ts > sfz_pod_ts and tot == 0:
                stz_pod_ts = ts

    # events.jsonl: SuccessfulRescale (New size: 3 / 0)
    scale_up_event = None
    scale_down_event = None
    if events_file.exists():
        with open(events_file) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("reason") != "SuccessfulRescale":
                    continue
                ts = parse_ts(e["ts"])
                if ts < sync_ts:
                    continue
                msg = e.get("message", "")
                if scale_up_event is None and "New size: 3" in msg:
                    scale_up_event = ts
                elif scale_up_event is not None and scale_down_event is None and "New size: 0" in msg:
                    scale_down_event = ts

    # メトリクス計算
    # Scale from Zero (event): sync_ts → scale-up イベント
    sfz_event = (scale_up_event - sync_ts).total_seconds() if scale_up_event else None
    # Scale from Zero (pod observed): sync_ts → replicas>=1 観測
    sfz_pod = (sfz_pod_ts - sync_ts).total_seconds() if sfz_pod_ts else None
    # Scale to Zero (event): push_0 → scale-down イベント
    stz_event = (scale_down_event - push_0_ts).total_seconds() if scale_down_event and push_0_ts else None
    # Scale to Zero (pod observed): push_0 → replicas=0 観測
    stz_pod = (stz_pod_ts - push_0_ts).total_seconds() if stz_pod_ts and push_0_ts else None

    return {
        "run_id": rundir.name,
        "sync_ts": sync_ts.isoformat(),
        "push_0_ts": push_0_ts.isoformat() if push_0_ts else None,
        "sfz_event_s": sfz_event,
        "sfz_pod_s": sfz_pod,
        "stz_event_s": stz_event,
        "stz_pod_s": stz_pod,
    }


def fmt_stats(values, unit="s"):
    vs = [v for v in values if v is not None]
    if not vs:
        return "n/a"
    if len(vs) == 1:
        return f"{vs[0]:.1f}{unit}"
    avg = statistics.mean(vs)
    sd = statistics.stdev(vs) if len(vs) >= 2 else 0
    return f"avg {avg:.1f}{unit} (min {min(vs):.1f} / max {max(vs):.1f} / σ {sd:.2f})"


def main():
    if len(sys.argv) < 2:
        print("Usage: lt-aggregate.py <batch_dir>", file=sys.stderr)
        sys.exit(2)

    batch_dir = Path(sys.argv[1])
    run_dirs = sorted([p for p in batch_dir.iterdir() if p.is_dir() and p.name.startswith("run-")])
    if not run_dirs:
        print(f"No run-* directories under {batch_dir}", file=sys.stderr)
        sys.exit(2)

    results = [analyze_run(rd) for rd in run_dirs]

    out_lines = []
    out_lines.append(f"# LT Pushgateway 構成 統計サマリ ({len(results)} runs)\n")
    out_lines.append(f"対象 run: {', '.join(r['run_id'] for r in results)}\n")
    out_lines.append("\n## 計測の定義\n")
    out_lines.append("- `Scale from Zero`: push queue_length=50 完了直後 (sync_ts) → HPA が scale up を発火した時刻")
    out_lines.append("- `Scale to Zero`: push queue_length=0 完了時刻 → HPA が scale down を発火した時刻")
    out_lines.append("- `*_event`: events.jsonl の SuccessfulRescale 時刻")
    out_lines.append("- `*_pod`: CSV で replicas が変化したのを sampling (5s 間隔) で観測した時刻")

    out_lines.append("\n## 各 run の主要メトリクス (秒)\n")
    header = "| メトリクス | " + " | ".join(r["run_id"] for r in results) + " | 統計 |"
    sep = "|---|" + "|".join(["---"] * len(results)) + "|---|"
    out_lines.append(header)
    out_lines.append(sep)

    keys = [
        ("sfz_event_s", "Scale from Zero (sync → SuccessfulRescale)"),
        ("sfz_pod_s",   "Scale from Zero (sync → first Pod observed)"),
        ("stz_event_s", "Scale to Zero (push 0 → SuccessfulRescale 'New size: 0')"),
        ("stz_pod_s",   "Scale to Zero (push 0 → replicas=0 observed)"),
    ]
    for key, label in keys:
        values = [r.get(key) for r in results]
        row = " | ".join(f"{v:.1f}" if v is not None else "n/a" for v in values)
        out_lines.append(f"| {label} | {row} | {fmt_stats(values)} |")

    out_lines.append("\n## 観察ポイント\n")
    out_lines.append("- Scale from Zero (event): Prometheus scrape (15s) + HPA poll (15s) の合算下限〜中央 (実測平均がここに来る)")
    out_lines.append("- Scale to Zero (event): adapter 経路遅延 (~15s) + stabilizationWindowSeconds (60s) の合算下限")
    out_lines.append("- Scale to Zero (pod observed): 上記 + sampling 遅延 5s 程度")
    out_lines.append("- σ が小さいほど決定論的タイマーの寄与が大きい (Scale to Zero は σ 小、Scale from Zero は σ 大の傾向)")

    out_file = batch_dir / "lt-stats.md"
    out_file.write_text("\n".join(out_lines) + "\n")
    print(f"Saved: {out_file}")
    print()
    print("\n".join(out_lines))


if __name__ == "__main__":
    main()
