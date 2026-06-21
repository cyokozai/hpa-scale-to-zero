# 2 VM 検証 統計サマリ (0 runs)

対象 run: 


## 各 run の主要メトリクス (秒)


| メトリクス |  | 統計 |
|---||---|
| HPA Scale from Zero (sync → SuccessfulRescale) |  | n/a |
| HPA Scale from Zero (sync → first Pod observed) |  | n/a |
| HPA Scale to Zero (scale-up → SuccessfulRescale 'New size: 0') |  | n/a |
| HPA Scale to Zero (scale-up → replicas=0 observed) |  | n/a |
| KEDA Scale from Zero (sync → KEDAScaleTargetActivated) |  | n/a |
| KEDA Scale from Zero (sync → first Pod observed) |  | n/a |
| KEDA Scale to Zero (Activated → Deactivated) |  | n/a |
| KEDA Scale to Zero (first Pod → replicas=0 observed) |  | n/a |

## 観察ポイント

- `*_event_s`: スケーラー (HPA / KEDA) が発火イベントを発行したタイミング
- `*_pod_s`: Pod の replicas 値が変化したことを measure.sh が観測したタイミング (sampling interval=5s の制約あり)
- KEDA `Scale to Zero` の `Activated → Deactivated` は cooldownPeriod=60s + Producer 消費時間に対応
- HPA `Scale to Zero` の `scale-up → 'New size: 0'` は stabilizationWindowSeconds=60s + Producer 消費 + 経路遅延に対応
- 完全フェアな比較ではないが、各スケーラーの「観測 → 反応 → 実行」までの時間特性を確認できる
