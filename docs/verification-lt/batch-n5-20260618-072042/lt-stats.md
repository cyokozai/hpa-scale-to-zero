# LT Pushgateway 構成 統計サマリ (5 runs)

対象 run: run-1, run-2, run-3, run-4, run-5


## 計測の定義

- `Scale from Zero`: push queue_length=50 完了直後 (sync_ts) → HPA が scale up を発火した時刻
- `Scale to Zero`: push queue_length=0 完了時刻 → HPA が scale down を発火した時刻
- `*_event`: events.jsonl の SuccessfulRescale 時刻
- `*_pod`: CSV で replicas が変化したのを sampling (5s 間隔) で観測した時刻

## 各 run の主要メトリクス (秒)

| メトリクス | run-1 | run-2 | run-3 | run-4 | run-5 | 統計 |
|---|---|---|---|---|---|---|
| Scale from Zero (sync → SuccessfulRescale) | 15.0 | 9.0 | 4.0 | 11.0 | 3.0 | avg 8.4s (min 3.0 / max 15.0 / σ 4.98) |
| Scale from Zero (sync → first Pod observed) | 18.0 | 11.0 | 6.0 | 12.0 | 5.0 | avg 10.4s (min 5.0 / max 18.0 / σ 5.22) |
| Scale to Zero (push 0 → SuccessfulRescale 'New size: 0') | 52.0 | 51.0 | 58.0 | 50.0 | 59.0 | avg 54.0s (min 50.0 / max 59.0 / σ 4.18) |
| Scale to Zero (push 0 → replicas=0 observed) | 55.0 | 55.0 | 60.0 | 55.0 | 61.0 | avg 57.2s (min 55.0 / max 61.0 / σ 3.03) |

## 観察ポイント

- Scale from Zero (event): Prometheus scrape (15s) + HPA poll (15s) の合算下限〜中央 (実測平均がここに来る)
- Scale to Zero (event): adapter 経路遅延 (~15s) + stabilizationWindowSeconds (60s) の合算下限
- Scale to Zero (pod observed): 上記 + sampling 遅延 5s 程度
- σ が小さいほど決定論的タイマーの寄与が大きい (Scale to Zero は σ 小、Scale from Zero は σ 大の傾向)
