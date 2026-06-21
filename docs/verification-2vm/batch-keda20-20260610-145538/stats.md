# 2 VM 検証 統計サマリ (20 runs)

対象 run: run-keda20-01, run-keda20-02, run-keda20-03, run-keda20-04, run-keda20-05, run-keda20-06, run-keda20-07, run-keda20-08, run-keda20-09, run-keda20-10, run-keda20-11, run-keda20-12, run-keda20-13, run-keda20-14, run-keda20-15, run-keda20-16, run-keda20-17, run-keda20-18, run-keda20-19, run-keda20-20


## 各 run の主要メトリクス (秒)


| メトリクス | run-keda20-01 | run-keda20-02 | run-keda20-03 | run-keda20-04 | run-keda20-05 | run-keda20-06 | run-keda20-07 | run-keda20-08 | run-keda20-09 | run-keda20-10 | run-keda20-11 | run-keda20-12 | run-keda20-13 | run-keda20-14 | run-keda20-15 | run-keda20-16 | run-keda20-17 | run-keda20-18 | run-keda20-19 | run-keda20-20 | 統計 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HPA Scale from Zero (sync → SuccessfulRescale) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| HPA Scale from Zero (sync → first Pod observed) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| HPA Scale to Zero (scale-up → SuccessfulRescale 'New size: 0') | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| HPA Scale to Zero (scale-up → replicas=0 observed) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| KEDA Scale from Zero (sync → KEDAScaleTargetActivated) | 14.0 | 14.0 | 14.0 | 13.0 | 14.0 | 14.0 | 14.0 | 14.0 | 14.0 | 14.0 | 14.0 | 15.0 | 15.0 | 15.0 | 14.0 | 15.0 | 14.0 | 15.0 | 15.0 | 15.0 | avg 14.3s (min 13.0 / max 15.0 / σ 0.57) |
| KEDA Scale from Zero (sync → first Pod observed) | 17.0 | 17.0 | 17.0 | 16.0 | 16.0 | 17.0 | 16.0 | 17.0 | 16.0 | 17.0 | 16.0 | 17.0 | 17.0 | 17.0 | 16.0 | 16.0 | 16.0 | 17.0 | 17.0 | 17.0 | avg 16.6s (min 16.0 / max 17.0 / σ 0.50) |
| KEDA Scale to Zero (Activated → Deactivated) | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | 60.0 | avg 60.0s (min 60.0 / max 60.0 / σ 0.00) |
| KEDA Scale to Zero (first Pod → replicas=0 observed) | 62.0 | 61.0 | 58.0 | 59.0 | 63.0 | 61.0 | 61.0 | 60.0 | 61.0 | 61.0 | 61.0 | 61.0 | 61.0 | 58.0 | 63.0 | 64.0 | 63.0 | 61.0 | 61.0 | 61.0 | avg 61.0s (min 58.0 / max 64.0 / σ 1.54) |

## 観察ポイント

- `*_event_s`: スケーラー (HPA / KEDA) が発火イベントを発行したタイミング
- `*_pod_s`: Pod の replicas 値が変化したことを measure.sh が観測したタイミング (sampling interval=5s の制約あり)
- KEDA `Scale to Zero` の `Activated → Deactivated` は cooldownPeriod=60s + Producer 消費時間に対応
- HPA `Scale to Zero` の `scale-up → 'New size: 0'` は stabilizationWindowSeconds=60s + Producer 消費 + 経路遅延に対応
- 完全フェアな比較ではないが、各スケーラーの「観測 → 反応 → 実行」までの時間特性を確認できる
