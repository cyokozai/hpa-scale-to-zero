# 2 VM 検証 統計サマリ (5 runs)

対象 run: run-1, run-2, run-3, run-4, run-5


## 各 run の主要メトリクス (秒)


| メトリクス | run-1 | run-2 | run-3 | run-4 | run-5 | 統計 |
|---|---|---|---|---|---|---|
| HPA Scale from Zero (sync → SuccessfulRescale) | 15.0 | 9.0 | 4.0 | 11.0 | 3.0 | avg 8.4s (min 3.0 / max 15.0 / σ 4.98) |
| HPA Scale from Zero (sync → first Pod observed) | 18.0 | 11.0 | 6.0 | 12.0 | 5.0 | avg 10.4s (min 5.0 / max 18.0 / σ 5.22) |
| HPA Scale to Zero (scale-up → SuccessfulRescale 'New size: 0') | 135.0 | 136.0 | 150.0 | 135.0 | 150.0 | avg 141.2s (min 135.0 / max 150.0 / σ 8.04) |
| HPA Scale to Zero (scale-up → replicas=0 observed) | 135.0 | 138.0 | 150.0 | 139.0 | 150.0 | avg 142.4s (min 135.0 / max 150.0 / σ 7.09) |
| KEDA Scale from Zero (sync → KEDAScaleTargetActivated) | n/a | n/a | n/a | n/a | n/a | n/a |
| KEDA Scale from Zero (sync → first Pod observed) | n/a | n/a | n/a | n/a | n/a | n/a |
| KEDA Scale to Zero (Activated → Deactivated) | n/a | n/a | n/a | n/a | n/a | n/a |
| KEDA Scale to Zero (first Pod → replicas=0 observed) | n/a | n/a | n/a | n/a | n/a | n/a |

## 観察ポイント

- `*_event_s`: スケーラー (HPA / KEDA) が発火イベントを発行したタイミング
- `*_pod_s`: Pod の replicas 値が変化したことを measure.sh が観測したタイミング (sampling interval=5s の制約あり)
- KEDA `Scale to Zero` の `Activated → Deactivated` は cooldownPeriod=60s + Producer 消費時間に対応
- HPA `Scale to Zero` の `scale-up → 'New size: 0'` は stabilizationWindowSeconds=60s + Producer 消費 + 経路遅延に対応
- 完全フェアな比較ではないが、各スケーラーの「観測 → 反応 → 実行」までの時間特性を確認できる
