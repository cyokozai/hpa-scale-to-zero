#!/usr/bin/env python3
"""
HPA Scale to Zero 計測結果の可視化 (5 figures)。

各 run の measurement.csv + triggers.csv から 2 つの指標を抽出し、
<batch_dir>/figures/ に 5 PNG を書き出す:

  fig1-bar.png            Scale from/to Zero の avg ± σ (棒グラフ + エラーバー)
  fig2-boxplot.png        箱ひげ図 (n の分散・外れ値)
  fig3-histogram.png      頻度分布 (決定論性の可視化)
  fig4-representative.png 中央値に近い 1 run の replicas 推移
  fig5-overlay.png        全 run の replicas 推移を重ね描き

使い方:
  python3 scripts/plot.py <batch_dir>

依存: matplotlib, numpy
  ローカルを汚さないため必ず venv 上で実行する:
    python3 -m venv .venv
    .venv/bin/pip install matplotlib numpy
    .venv/bin/python scripts/plot.py /tmp/measure-batch
"""
import csv
import statistics
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_run(rundir: Path) -> dict | None:
    csv_file = rundir / "measurement.csv"
    triggers_file = rundir / "triggers.csv"
    if not csv_file.exists() or not triggers_file.exists():
        return None

    # triggers.csv → push 50 / push 0 のタイムスタンプ
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

    # measurement.csv → 時系列 + 遷移時刻
    samples = []
    sfz_ts = stz_ts = None
    with open(csv_file) as f:
        for row in csv.DictReader(f):
            ts = parse_ts(row["timestamp"])
            rep = int(row["replicas"])
            samples.append({"ts": ts, "phase": row["phase"], "replicas": rep})
            if sfz_ts is None and ts >= push_50 and rep >= 1:
                sfz_ts = ts
            if stz_ts is None and ts >= push_0 and rep == 0:
                stz_ts = ts

    return {
        "run_id": rundir.name,
        "push_50": push_50,
        "push_0": push_0,
        "samples": samples,
        "sfz": (sfz_ts - push_50).total_seconds() if sfz_ts else None,
        "stz": (stz_ts - push_0).total_seconds() if stz_ts else None,
    }


def load_all(batch_dir: Path) -> list[dict]:
    runs = []
    for rd in sorted(p for p in batch_dir.iterdir() if p.is_dir() and p.name.startswith("run-")):
        r = load_run(rd)
        if r:
            runs.append(r)
    return runs


# ---------- Figures ----------

def fig1_bar(runs: list[dict], out: Path):
    sfz = [r["sfz"] for r in runs if r["sfz"] is not None]
    stz = [r["stz"] for r in runs if r["stz"] is not None]

    labels = ["Scale from Zero", "Scale to Zero"]
    avgs = [statistics.mean(sfz), statistics.mean(stz)]
    sds = [statistics.stdev(sfz) if len(sfz) >= 2 else 0,
           statistics.stdev(stz) if len(stz) >= 2 else 0]
    ns = [len(sfz), len(stz)]

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(labels))
    bars = ax.bar(x, avgs, yerr=sds, capsize=10,
                  color=["#3b7", "#d63"], edgecolor="black", linewidth=0.6)
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Latency (sec)")
    ax.set_title(f"HPA Scale to Zero (n={len(runs)})  avg ± σ")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    for bar, avg, sd, n in zip(bars, avgs, sds, ns):
        ax.text(bar.get_x() + bar.get_width()/2, avg + sd + 2,
                f"{avg:.1f} ± {sd:.1f}s\nn={n}", ha="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def fig2_boxplot(runs: list[dict], out: Path):
    sfz = [r["sfz"] for r in runs if r["sfz"] is not None]
    stz = [r["stz"] for r in runs if r["stz"] is not None]

    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot([sfz, stz], labels=["Scale from Zero", "Scale to Zero"],
                    patch_artist=True, widths=0.5,
                    medianprops={"color": "black", "linewidth": 1.5})
    for patch, color in zip(bp["boxes"], ["#3b7", "#d63"]):
        patch.set_facecolor(color); patch.set_alpha(0.65)
    ax.set_ylabel("Latency (sec)")
    ax.set_title(f"分散と中央値 (n={len(runs)})")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def fig3_histogram(runs: list[dict], out: Path):
    sfz = [r["sfz"] for r in runs if r["sfz"] is not None]
    stz = [r["stz"] for r in runs if r["stz"] is not None]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    if sfz:
        a1.hist(sfz, bins=range(0, max(int(max(sfz)) + 5, 30), 2),
                color="#3b7", edgecolor="black", alpha=0.8)
        a1.axvline(statistics.mean(sfz), color="red", linestyle="--",
                   label=f"avg {statistics.mean(sfz):.1f}s")
        a1.legend()
    a1.set_title(f"Scale from Zero (n={len(sfz)})")
    a1.set_xlabel("Latency (sec)"); a1.set_ylabel("Frequency")
    a1.grid(linestyle="--", alpha=0.5)

    if stz:
        a2.hist(stz, bins=range(max(0, int(min(stz)) - 5), int(max(stz)) + 5, 2),
                color="#d63", edgecolor="black", alpha=0.8)
        a2.axvline(statistics.mean(stz), color="red", linestyle="--",
                   label=f"avg {statistics.mean(stz):.1f}s")
        a2.legend()
    a2.set_title(f"Scale to Zero (n={len(stz)})")
    a2.set_xlabel("Latency (sec)"); a2.set_ylabel("Frequency")
    a2.grid(linestyle="--", alpha=0.5)

    fig.suptitle("頻度分布 (決定論性の可視化)", fontsize=12)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def fig4_representative(runs: list[dict], out: Path):
    valid = [r for r in runs if r["sfz"] is not None and r["stz"] is not None]
    if not valid:
        return
    median_sfz = statistics.median(r["sfz"] for r in valid)
    rep = min(valid, key=lambda r: abs(r["sfz"] - median_sfz))

    # x 軸: push 50 からの経過秒
    base = rep["push_50"]
    ts = [(s["ts"] - base).total_seconds() for s in rep["samples"]]
    replicas = [s["replicas"] for s in rep["samples"]]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.step(ts, replicas, where="post", color="#3b7", linewidth=2.2)
    ax.set_xlabel("時刻 (push 50 からの経過秒)")
    ax.set_ylabel("replicas")
    ax.set_ylim(-0.3, 3.3)
    ax.grid(linestyle="--", alpha=0.4)

    # マーカ: push 0 と scale-up / scale-down
    push_0_t = (rep["push_0"] - base).total_seconds()
    ax.axvline(rep["sfz"], color="blue", linestyle="--", alpha=0.6)
    ax.text(rep["sfz"], 3.1, f"  scale up +{rep['sfz']:.1f}s",
            fontsize=9, color="blue")
    ax.axvline(push_0_t, color="orange", linestyle="--", alpha=0.6)
    ax.text(push_0_t, 1.5, "  push 0", fontsize=9, color="orange")
    ax.axvline(push_0_t + rep["stz"], color="red", linestyle="--", alpha=0.6)
    ax.text(push_0_t + rep["stz"], 0.3, f"  scale down +{rep['stz']:.1f}s",
            fontsize=9, color="red")

    ax.set_title(f"代表 run ({rep['run_id']}, 中央値 {median_sfz:.1f}s)")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def fig5_overlay(runs: list[dict], out: Path):
    fig, ax = plt.subplots(figsize=(11, 5))
    cmap = plt.get_cmap("viridis")
    for i, r in enumerate(runs):
        base = r["push_50"]
        ts = [(s["ts"] - base).total_seconds() for s in r["samples"]]
        replicas = [s["replicas"] for s in r["samples"]]
        ax.step(ts, replicas, where="post",
                color=cmap(i / max(len(runs)-1, 1)), alpha=0.55, linewidth=1.0)
    ax.set_xlabel("時刻 (push 50 からの経過秒)")
    ax.set_ylabel("replicas")
    ax.set_ylim(-0.3, 3.3)
    ax.set_title(f"全 {len(runs)} run の replicas 推移 (重ね描き)")
    ax.grid(linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def main():
    if len(sys.argv) < 2:
        print("Usage: plot.py <batch_dir>", file=sys.stderr)
        sys.exit(2)

    batch_dir = Path(sys.argv[1])
    runs = load_all(batch_dir)
    if not runs:
        print(f"No valid runs found in {batch_dir}", file=sys.stderr)
        sys.exit(2)
    print(f"Loaded {len(runs)} runs")

    out_dir = batch_dir / "figures"
    out_dir.mkdir(exist_ok=True)

    figs = [
        ("fig1-bar.png", fig1_bar),
        ("fig2-boxplot.png", fig2_boxplot),
        ("fig3-histogram.png", fig3_histogram),
        ("fig4-representative.png", fig4_representative),
        ("fig5-overlay.png", fig5_overlay),
    ]
    for name, fn in figs:
        fn(runs, out_dir / name)
        print(f"Saved: {out_dir / name}")


if __name__ == "__main__":
    main()
