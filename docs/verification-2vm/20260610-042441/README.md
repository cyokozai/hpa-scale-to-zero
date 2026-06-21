# Run 20260610-042441

- **Sync target**: 2026-06-10T04:25:13Z
- **Duration**: 300s
- **Interval**: 5s
- **hpa-test** (HPA + prometheus-adapter): cyokozai@10.2.128.155
- **keda-test** (KEDA Operator): cyokozai@10.2.128.159

## Files

- `hpa.csv` / `keda.csv` — 時系列 CSV (replicas, desired_replicas, hpa_targets, hpa_conditions, lag, pod phases)
- `hpa-events.jsonl` / `keda-events.jsonl` — `kubectl get events --watch` の jsonl
- `hpa-run.log` / `keda-run.log` — VM 上の measure.sh 実行ログ
- `merged-timeline.md` — 両 VM を timestamp で揃えた表形式タイムライン

## 検証シナリオ

1. baseline: 両 VM が replicas=0 (Scale to Zero 状態) で開始
2. START_AT 同期: 両 VM が同じ秒に Producer Job を投入
3. 300s 間、5s 間隔で計測
4. Producer Job 削除後、Scale to Zero を観測

## 観察ポイント

- **Scale from Zero レイテンシ**: Producer 投入から replicas>=1 までの秒数
- **Scale up step 数**: HPA は 1 ステップ (0→N)、KEDA は 2 ステップ (0→1→N)
- **HPA Conditions 遷移**: AbleToScale, ScalingActive, ScaledToZero の状態変化
- **Scale to Zero 発火時刻**: lag=0 からの待機時間 (60s)
