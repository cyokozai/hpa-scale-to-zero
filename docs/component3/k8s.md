# Component 3: Scale to Zero 実行パス — K8s v1.36 側

## 概要

K8s の Scale to Zero は `reconcileAutoscaler()` の通常スケールループの **延長線上**にある。
特別な「ゼロスケール専用コードパス」は存在せず、
`desiredReplicas == 0` になった場合も同じ `scale.Update()` API を通じてスケールする。
スケール後に `ScaledToZero=True` Condition を記録することで、次回の「復帰可否チェック」に備える。

```
reconcileAutoscaler()
  │
  ├─ [1] computeReplicasForMetrics()
  │       └─ GetExternalMetricReplicas()
  │            lag=0 → usageRatio=0 → desiredReplicas=0
  │
  ├─ [2] normalizeDesiredReplicasWithBehaviors()
  │       minReplicas=0 の場合に限り 0 を許容
  │
  ├─ [3] scale.Update()          ← 通常スケールと同じ API
  │       Deployment.Spec.Replicas = 0
  │
  └─ [4] setCondition(ScaledToZero=True)  ← 復帰のための状態記録
```

---

## ファイル

| ファイル | 役割 |
|---|---|
| `pkg/controller/podautoscaler/horizontal.go` | Scale to Zero 実行 + Condition 記録 |
| `pkg/controller/podautoscaler/replica_calculator.go` | lag=0 → desiredReplicas=0 の計算 |

---

## [1] lag=0 → desiredReplicas=0 の計算パス
### `pkg/controller/podautoscaler/replica_calculator.go:354`

```go
func (c *ReplicaCalculator) GetExternalMetricReplicas(...) (int32, int64, time.Time, error) {
    // External Metrics API から lag 値を取得（KEDA の metrics-apiserver 経由）
    metrics, _, err := c.metricsClient.GetExternalMetric(metricName, namespace, metricLabelSelector)

    // 全パーティションの lag を合計
    usage = 0
    for _, val := range metrics {
        usage += val
    }
    // lag = 0 のとき: usage = 0

    // usageRatio = usage / targetUsage = 0 / N = 0
    usageRatio := float64(usage) / float64(targetUsage)

    // usageRatio = 0 → desiredReplicas = ceil(currentReplicas * 0) = 0
    replicaCount, timestamp, err = c.getUsageRatioReplicaCount(currentReplicas, usageRatio, ...)
    return replicaCount, usage, timestamp, err
}
```

**ポイント:**
- lag=0 の場合、`usageRatio=0` → `desiredReplicas=0`。
  これは「スケールダウン」として扱われ、通常のスケールダウンロジックを通過する。
- Kafka のすべてのパーティションで consumer が追いついている状態（lag=0）が Scale to Zero のトリガー。

---

## [2] desiredReplicas=0 の正規化
### `pkg/controller/podautoscaler/horizontal.go:925`

```go
// normalizeDesiredReplicasWithBehaviors で desiredReplicas=0 が通過するための条件
if hpa.Spec.Behavior == nil {
    desiredReplicas = a.normalizeDesiredReplicas(hpa, key, currentReplicas, desiredReplicas, minReplicas)
} else {
    desiredReplicas = a.normalizeDesiredReplicasWithBehaviors(hpa, key, currentReplicas, desiredReplicas, minReplicas)
}
// minReplicas=0 の場合のみ desiredReplicas=0 が通過する。
// minReplicas>=1 の場合は max(desiredReplicas, minReplicas) = minReplicas になり 0 にならない。
```

**ポイント:**
- HPA の `spec.minReplicas` を **0 に設定すること** が Scale to Zero の必要条件。
- `minReplicas=0` は通常の HPA では許容されないが、`HPAScaleToZero` Feature Gate が有効の場合に限り
  Object/External メトリクスを持つ HPA で許容される。

---

## [3] scale.Update() — スケール実行
### `pkg/controller/podautoscaler/horizontal.go:939`

```go
if rescale {
    err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
        scale.Spec.Replicas = desiredReplicas  // = 0

        // scale subresource を通じて Deployment.Spec.Replicas を更新
        _, updateErr := a.scaleNamespacer.Scales(hpa.Namespace).Update(ctx, targetGR, scale, metav1.UpdateOptions{})
        if updateErr == nil {
            return nil
        }
        // 競合エラー時は最新版を取得してリトライ
        latestScale, _ = a.scaleNamespacer.Scales(hpa.Namespace).Get(...)
        scale = latestScale
        return updateErr
    })
}
```

**ポイント:**
- `retry.RetryOnConflict` による楽観的ロック対応。
  Deployment が同時に更新された場合でも競合エラーをリトライで解消する。
- `scale subresource` は K8s 標準の抽象 API。Deployment・StatefulSet など任意のリソースに対して
  `Replicas` のみを更新できる。HPA はターゲットリソースの実装に依存しない。
- **クールダウン期間がない**。メトリクスが 0 を返した瞬間にスケールが始まる。
  （スケールダウン遅延は `behavior.scaleDown.stabilizationWindowSeconds` で設定可能）

---

## [4] ScaledToZero Condition の記録
### `pkg/controller/podautoscaler/horizontal.go:988`

```go
// ゼロへのスケールが成功した後
if scaleToZeroFeatureEnabled {
    if currentReplicas > 0 && desiredReplicas == 0 && minReplicas == 0 && hasObjectOrExtMetrics {
        // 4条件すべてを満たした場合のみ True を記録
        setCondition(hpa, autoscalingv2.ScaledToZero, v1.ConditionTrue,
            "ScaledToZero", "the HPA controller scaled the workload to zero")
    } else {
        // 復帰後はすぐに False に上書き（次回以降のゼロスケール時に再設定される）
        setCondition(hpa, autoscalingv2.ScaledToZero, v1.ConditionFalse,
            "NotScaledToZero", "the HPA controller did not scale the workload to zero")
    }
}
```

**ポイント:**
- この Condition を記録しないと **次回のスケールループで `canScaleFromZero=false`** となり
  永久にゼロのままになる（Component 2 参照）。
- 4条件チェック:
  1. `currentReplicas > 0`: ゼロへの**移行**のときのみ（すでに 0 なら記録しない）
  2. `desiredReplicas == 0`: 実際にゼロになること
  3. `minReplicas == 0`: Scale to Zero を意図した設定
  4. `hasObjectOrExtMetrics`: Object/External メトリクス存在（v1.36 alpha の制約）

---

## Scale to Zero の全体シーケンス（K8s）

```
[前提条件]
  - HPA spec.minReplicas = 0
  - HPAScaleToZero Feature Gate = true
  - メトリクス種別 = ExternalMetricSourceType（Kafka lag）

[ループ周期: --horizontal-pod-autoscaler-sync-period, デフォルト 15s]

reconcileAutoscaler()
  │
  ├─ scaleToZeroFeatureEnabled = true     (Feature Gate チェック)
  ├─ hasObjectOrExtMetrics = true         (Kafka = External metrics)
  ├─ scaledToZeroCondition = false        (まだスケールしていない)
  ├─ canScaleFromZero = false             (スケールトゥゼロしていない)
  │
  ├─ currentReplicas=N > 0               → shouldComputeMetricsForZeroReplicas はスキップ
  │                                         needsMetricComputation=true のまま
  │
  ├─ computeReplicasForMetrics()
  │    └─ GetExternalMetricReplicas()
  │         lag=0 → usageRatio=0 → desiredReplicas=0
  │
  ├─ normalizeDesiredReplicasWithBehaviors()
  │    minReplicas=0 → desiredReplicas=0 を通過
  │
  ├─ rescale=true (desiredReplicas=0 != currentReplicas=N)
  │
  ├─ scale.Update(Replicas=0)
  │    → Deployment controller が Pod を停止
  │
  └─ setCondition(ScaledToZero=True)    ← 復帰に必要な状態を HPA に記録
```

---

## KEDA との比較（Scale to Zero）

| 観点 | K8s v1.36 | KEDA v2.16 |
|---|---|---|
| 実行パス | 通常スケールループと同一 | `scaleToZeroOrIdle()` 専用関数 |
| クールダウン | なし（`behavior.scaleDown` で設定可） | あり（デフォルト **5 分**） |
| スケール API | scale subresource（抽象） | scale subresource（同じ） |
| 状態記録 | `ScaledToZero Condition=True` | `ActiveCondition=False` + Event |
| ゼロの判断根拠 | `desiredReplicas==0`（メトリクス計算） | `isActive=false`（Scaler の bool） |
| minReplicas 要件 | `spec.minReplicas=0` 必須 | `spec.minReplicaCount=0` 必須 |
