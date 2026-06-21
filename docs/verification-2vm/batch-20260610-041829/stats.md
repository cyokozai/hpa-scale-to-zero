# 2 VM 検証 統計サマリ (3 runs)

対象 run: 20260610-041830, 20260610-042441, 20260610-043049


## 各 run の主要メトリクス (秒)


| メトリクス | 20260610-041830 | 20260610-042441 | 20260610-043049 | 統計 |
|---|---|---|---|---|
| HPA Scale from Zero (sync → SuccessfulRescale) | 31.0 | 23.0 | 30.0 | avg 28.0s (min 23.0 / max 31.0 / σ 4.36) |
| HPA Scale from Zero (sync → first Pod observed) | 32.0 | 27.0 | 34.0 | avg 31.0s (min 27.0 / max 34.0 / σ 3.61) |
| HPA Scale to Zero (scale-up → SuccessfulRescale 'New size: 0') | 75.0 | 75.0 | 75.0 | avg 75.0s (min 75.0 / max 75.0 / σ 0.00) |
| HPA Scale to Zero (scale-up → replicas=0 observed) | 78.0 | 72.0 | 76.0 | avg 75.3s (min 72.0 / max 78.0 / σ 3.06) |
| KEDA Scale from Zero (sync → KEDAScaleTargetActivated) | 22.0 | 13.0 | 20.0 | avg 18.3s (min 13.0 / max 22.0 / σ 4.73) |
| KEDA Scale from Zero (sync → first Pod observed) | 23.0 | 17.0 | 22.0 | avg 20.7s (min 17.0 / max 23.0 / σ 3.21) |
| KEDA Scale to Zero (Activated → Deactivated) | 60.0 | 60.0 | 60.0 | avg 60.0s (min 60.0 / max 60.0 / σ 0.00) |
| KEDA Scale to Zero (first Pod → replicas=0 observed) | 65.0 | 57.0 | 59.0 | avg 60.3s (min 57.0 / max 65.0 / σ 4.16) |

## 観察ポイント

- `*_event_s`: スケーラー (HPA / KEDA) が発火イベントを発行したタイミング
- `*_pod_s`: Pod の replicas 値が変化したことを measure.sh が観測したタイミング (sampling interval=5s の制約あり)
- KEDA `Scale to Zero` の `Activated → Deactivated` は cooldownPeriod=60s + Producer 消費時間に対応
- HPA `Scale to Zero` の `scale-up → 'New size: 0'` は stabilizationWindowSeconds=60s + Producer 消費 + 経路遅延に対応
- 完全フェアな比較ではないが、各スケーラーの「観測 → 反応 → 実行」までの時間特性を確認できる
