# Component 4: Scale from Zero 実行パス — K8s v1.36 側

## 概要

K8s の Scale from Zero は、Scale to Zero と同様に `reconcileAutoscaler()` の通常ループで処理される。
`canScaleFromZero=true` の場合にメトリクス計算を許可し、
lag > 0 → `desiredReplicas > 0` となった瞬間に `scale.Update()` を呼んで復帰する。
KEDA と異なり、**最初から「メトリクスが示す適切なレプリカ数」まで一気にスケールアップする**のが特徴。

```
reconcileAutoscaler() [currentReplicas=0, canScaleFromZero=true]
  │
  ├─ [1] shouldComputeMetricsForZeroReplicas() → (true, false)
  │       条件: canScaleFromZero = ScaledToZero Condition=True + Object/External メトリクスあり
  │
  ├─ [2] computeReplicasForMetrics()
  │       └─ GetExternalMetricReplicas(currentReplicas=0)
  │            └─ getUsageRatioReplicaCount(currentReplicas=0, usageRatio=lag/target)
  │                 → ceil(lag / targetUsage)   ← currentReplicas=0 専用パス
  │
  ├─ [3] minReplicas 補正
  │       desiredReplicas < minReplicas の場合 → desiredReplicas = minReplicas
  │
  ├─ [4] scale.Update(Replicas=desiredReplicas)
  │
  └─ [5] setCondition(ScaledToZero=False)
```

---

## ファイル

| ファイル | 役割 |
|---|---|
| `pkg/controller/podautoscaler/horizontal.go` | Scale from Zero のトリガー判断・実行・Condition 更新 |
| `pkg/controller/podautoscaler/replica_calculator.go` | `currentReplicas=0` 時のレプリカ計算専用パス |

---

## [1] canScaleFromZero の確認と計算許可
### `pkg/controller/podautoscaler/horizontal.go:856`

```go
// 事前条件の計算（Component 2 で解析済み）
scaleToZeroFeatureEnabled := utilfeature.DefaultFeatureGate.Enabled(features.HPAScaleToZero)
hasObjectOrExtMetrics := hasObjectOrExternalMetrics(hpa)
scaledToZeroCondition := scaleToZeroFeatureEnabled && getScaledToZeroConditionStatus(hpa)
canScaleFromZero := scaledToZeroCondition && hasObjectOrExtMetrics

// currentReplicas=0 のとき
needsMetricComputation, shouldDisable =
    a.shouldComputeMetricsForZeroReplicas(minReplicas, scaledToZeroCondition, canScaleFromZero)
// canScaleFromZero=true → (true, false) → メトリクス計算へ進む
```

**ポイント:**
- `ScaledToZero Condition=True` が存在しなければ `canScaleFromZero=false` になり、
  ゼロのまま永久に復帰できない。Scale to Zero 時に Condition を記録したこと（Component 3）が前提。
- Feature Gate が無効化されると `scaledToZeroCondition=false` になり、
  `ScaledToZero Condition` が残っていても復帰できなくなる。

---

## [2] currentReplicas=0 時の専用計算パス
### `pkg/controller/podautoscaler/replica_calculator.go:282`

```go
func (c *ReplicaCalculator) getUsageRatioReplicaCount(
    currentReplicas int32, usageRatio float64, ...) (replicaCount int32, ...) {

    if currentReplicas != 0 {
        // 通常スケール: tolerance チェック + readyPodCount を基準に計算
        if tolerances.isWithin(usageRatio) {
            return currentReplicas, timestamp, nil  // 変化量が小さければ据え置き
        }
        readyPodCount, _ := c.getReadyPodsCount(namespace, selector)
        replicaCount = int32(math.Ceil(usageRatio * float64(readyPodCount)))
    } else {
        // currentReplicas=0 専用パス: ceil(usageRatio) を直接返す
        // 実行中 Pod がいないため readyPodCount は 0 → 使えない
        replicaCount = int32(math.Ceil(usageRatio))
        // usageRatio = lag / targetUsage なので:
        // lag=500, target=100 → usageRatio=5 → replicaCount=5
        // lag=50,  target=100 → usageRatio=0.5 → replicaCount=1 (ceil で切り上げ)
    }
    return replicaCount, timestamp, err
}
```

**ポイント:**
- `currentReplicas=0` の場合は `readyPodCount` が取れないため、
  `ceil(usageRatio)` を直接 desiredReplicas として使う**専用パス**がある。
- lag が targetUsage より小さくても `ceil()` により必ず **1 以上**を返す（lag > 0 の場合）。
- tolerance チェックをスキップする（0 からの復帰は常にスケールが必要）。
- `usageRatio = lag(milli) / targetUsage(milli)` で計算されるため、
  lag の大きさに比例したレプリカ数で一気に復帰できる。

---

## [2'] GetExternalMetricReplicas の全体フロー
### `pkg/controller/podautoscaler/replica_calculator.go:354`

```go
func (c *ReplicaCalculator) GetExternalMetricReplicas(currentReplicas int32, ...) (...) {
    // External Metrics API から lag 値を取得
    metrics, _, _ := c.metricsClient.GetExternalMetric(metricName, namespace, ...)

    // 全パーティションの lag を合計
    usage = 0
    for _, val := range metrics {
        usage += val
    }
    // lag=500 (milli) の場合: usage=500

    // usageRatio = usage / targetUsage
    // targetUsage=100 (milli) の場合: usageRatio=5.0
    usageRatio := float64(usage) / float64(targetUsage)

    // currentReplicas=0 → ceil(5.0) = 5 レプリカを返す
    replicaCount, _, _ = c.getUsageRatioReplicaCount(currentReplicas, usageRatio, ...)
    return replicaCount, usage, timestamp, err
}
```

---

## [3] minReplicas 補正
### `pkg/controller/podautoscaler/horizontal.go:931`

```go
// ゼロから復帰する際、minReplicas 未満にならないよう補正
// ケース: ScaledToZero 状態中に minReplicas が 0→N に変更された場合
if currentReplicas == 0 && minReplicas != 0 && scaledToZeroCondition && desiredReplicas < minReplicas {
    desiredReplicas = minReplicas
    if rescaleReason == "" {
        rescaleReason = "Current number of replicas below Spec.MinReplicas"
    }
}
```

**ポイント:**
- 通常（minReplicas=0 でゼロスケールした場合）はこのブランチは通らない。
- ScaledToZero 状態の間に HPA の `minReplicas` が変更されたエッジケースへの対応。
- lag が小さくて `ceil(usageRatio)=1` でも `minReplicas=3` なら 3 まで補正される。

---

## [4][5] スケール実行と Condition 更新
### `pkg/controller/podautoscaler/horizontal.go:939`

```go
if rescale {  // desiredReplicas(>0) != currentReplicas(0) → true
    scale.Spec.Replicas = desiredReplicas
    _, err := a.scaleNamespacer.Scales(hpa.Namespace).Update(ctx, targetGR, scale, ...)

    // スケール成功後
    if scaleToZeroFeatureEnabled {
        // currentReplicas=0 → desiredReplicas>0 なのでこの条件は false
        // if currentReplicas > 0 && desiredReplicas == 0 && ...

        // → else ブランチ: ScaledToZero=False を記録（復帰完了を示す）
        setCondition(hpa, autoscalingv2.ScaledToZero, v1.ConditionFalse,
            "NotScaledToZero", "the HPA controller did not scale the workload to zero")
    }
}
```

**ポイント:**
- 復帰後に `ScaledToZero=False` を記録することで、次回ループ以降は
  `scaledToZeroCondition=false` になり `canScaleFromZero=false` になる。
  これにより「復帰済み」状態が正しく記録される。
- 次の Scale to Zero が発生するまで `ScaledToZero Condition` は False のまま。

---

## Scale from Zero の全体シーケンス（K8s）

```
[前提条件]
  - HPA Status.Conditions に ScaledToZero=True が存在
  - currentReplicas = 0
  - Kafka lag が 0 から復活（consumer group に未読メッセージが積まれた）

[ループ周期: --horizontal-pod-autoscaler-sync-period, デフォルト 15s]

reconcileAutoscaler()
  │
  ├─ scaledToZeroCondition = true   (ScaledToZero Condition=True を確認)
  ├─ canScaleFromZero = true        (+ hasObjectOrExtMetrics=true)
  │
  ├─ currentReplicas=0 → shouldComputeMetricsForZeroReplicas()
  │    canScaleFromZero=true → (needsMetricComputation=true, shouldDisable=false)
  │
  ├─ computeReplicasForMetrics()
  │    └─ GetExternalMetricReplicas(currentReplicas=0)
  │         lag=500, target=100
  │         usageRatio = 500/100 = 5.0
  │         currentReplicas=0 → replicaCount = ceil(5.0) = 5
  │
  ├─ normalizeDesiredReplicasWithBehaviors()
  │    behavior.scaleUp ポリシーに従い上限を適用
  │
  ├─ rescale=true (desiredReplicas=5 != currentReplicas=0)
  │
  ├─ scale.Update(Replicas=5)
  │    → Deployment controller が Pod を起動
  │
  └─ setCondition(ScaledToZero=False)
       → 次回ループから canScaleFromZero=false（通常スケールに戻る）
```

---

## KEDA との比較（Scale from Zero）

| 観点 | K8s v1.36 | KEDA v2.16 |
|---|---|---|
| 復帰トリガー | `ScaledToZero Condition=True` + 次ループでメトリクス計算 | `isActive=true` になった**その**ループで即座に実行 |
| 初回スケール先 | `ceil(lag / targetUsage)` → 適切なレプリカ数に一気に | `max(minReplicaCount, 1)` → まず最低限まで |
| その後のスケール | HPA が引き続き通常スケールで調整 | HPA が調整（KEDA は再び Deployment 直接操作をしない） |
| 復帰の速さ | 次の HPA sync（デフォルト 15s）まで待つ | 次の polling（デフォルト 30s）まで待つ |
| 状態リセット | `ScaledToZero=False` を記録 | `LastActiveTime` を更新 |
| Condition の役割 | 復帰の**必要条件**（ないと永久ゼロ） | 参照しない（`isActive` が全て） |
