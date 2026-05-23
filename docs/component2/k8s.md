# Component 2: スケール判断ロジック — K8s v1.36 側

## 概要

K8s HPA のスケール判断は `reconcileAutoscaler()` が一手に担う。
`HPAScaleToZero` Feature Gate を前提に、現在のレプリカ数・HPA の Condition・
メトリクスの種別（Object/External か否か）を組み合わせて「0 にする/0 から復帰する」を決める。
アクティブ判定は Scaler 内部では行わず、**このレイヤーで外部から判断する**のが KEDA との最大の差異。

```
reconcileAutoscaler()
  ├─ hasObjectOrExternalMetrics()        ← Object/External メトリクスの有無チェック
  ├─ getScaledToZeroConditionStatus()    ← HPA Condition から ScaledToZero=True を読む
  ├─ shouldComputeMetricsForZeroReplicas() ← ゼロ時にメトリクス計算するか判断
  ├─ computeReplicasForMetrics()         ← メトリクス取得 → desiredReplicas 算出
  └─ scale.Update() + setCondition()     ← スケール実行 + Condition 記録
```

---

## ファイル

| ファイル | 役割 |
|---|---|
| `pkg/controller/podautoscaler/horizontal.go` | HPA コントローラー全体（スケール判断・実行・Condition 管理） |

---

## hasObjectOrExternalMetrics — v1.36 alpha の制約チェック
### `pkg/controller/podautoscaler/horizontal.go:1548`

```go
// HPA Spec に Object または External メトリクスが1つでもあれば true を返す。
// v1.36 alpha では CPU/Memory のみの HPA は Scale to Zero 対象外。
func hasObjectOrExternalMetrics(hpa *autoscalingv2.HorizontalPodAutoscaler) bool {
    for _, metric := range hpa.Spec.Metrics {
        if metric.Type == autoscalingv2.ObjectMetricSourceType || metric.Type == autoscalingv2.ExternalMetricSourceType {
            return true
        }
    }
    return false
}
```

**ポイント:**
- この関数が `false` を返す = CPU/Memory のみの HPA = Scale to Zero 不可（Feature Gate が有効でも）。
- v1.36 で「Object/External メトリクスに限定」と明示されたのはここに対応する。

---

## getScaledToZeroConditionStatus — 復帰判断の鍵
### `pkg/controller/podautoscaler/horizontal.go:1558`

```go
// HPA の Status.Conditions に ScaledToZero=True が存在するかを返す。
// ゼロスケール後にのみ True になり、復帰後は False に上書きされる。
func getScaledToZeroConditionStatus(hpa *autoscalingv2.HorizontalPodAutoscaler) bool {
    for _, condition := range hpa.Status.Conditions {
        if condition.Type == autoscalingv2.ScaledToZero {
            return condition.Status == v1.ConditionTrue
        }
    }
    return false
}
```

**ポイント:**
- この Condition が存在しない（ゼロスケールしたことがない）と `canScaleFromZero=false` になり
  ゼロから復帰できない。ゼロスケール → Condition 記録 → 復帰 という順序依存がある。
- KEDA の `isActive` フラグとは対照的に、**HPA オブジェクト自体に状態を持つ**設計。

---

## shouldComputeMetricsForZeroReplicas — ゼロ時のメトリクス計算判断
### `pkg/controller/podautoscaler/horizontal.go:763`

```go
// currentReplicas==0 のときにメトリクス計算が必要かを返す。
// needsMetricComputation=false かつ shouldDisable=true → スケーリング無効（ゼロのまま）
// needsMetricComputation=true  かつ shouldDisable=false → メトリクスを計算して復帰判断
func (a *HorizontalController) shouldComputeMetricsForZeroReplicas(
    minReplicas int32,
    scaledToZeroCondition, canScaleFromZero bool,
) (needsMetricComputation bool, shouldDisable bool) {
    if (minReplicas != 0 && scaledToZeroCondition) || canScaleFromZero {
        return true, false
    }
    return false, true
}
```

**ポイント:**
- `canScaleFromZero = scaledToZeroCondition && hasObjectOrExtMetrics`
  → Feature Gate 有効 かつ ScaledToZero Condition が True かつ Object/External メトリクスあり
  → この条件が揃って初めてメトリクス計算に進む
- `minReplicas != 0 && scaledToZeroCondition` は「minReplicas>0 に変更されたが ScaledToZero は残っている」
  ケースへの対応。このとき desiredReplicas はメトリクス計算後に minReplicas 未満にはならない。

---

## reconcileAutoscaler — スケール判断のメインループ
### `pkg/controller/podautoscaler/horizontal.go:773`

```go
func (a *HorizontalController) reconcileAutoscaler(ctx context.Context, ...) (retErr error) {
    // ...

    // ① Scale to Zero 関連の事前条件を計算
    scaleToZeroFeatureEnabled := utilfeature.DefaultFeatureGate.Enabled(features.HPAScaleToZero)
    hasObjectOrExtMetrics := hasObjectOrExternalMetrics(hpa)
    // Feature Gate が有効 かつ HPA に ScaledToZero=True Condition がある
    scaledToZeroCondition := scaleToZeroFeatureEnabled && getScaledToZeroConditionStatus(hpa)
    // ScaledToZero 状態 かつ Object/External メトリクスがある → 復帰可能
    canScaleFromZero := scaledToZeroCondition && hasObjectOrExtMetrics

    // ② currentReplicas==0 の場合の分岐
    if currentReplicas == 0 {
        needsMetricComputation, shouldDisable =
            a.shouldComputeMetricsForZeroReplicas(minReplicas, scaledToZeroCondition, canScaleFromZero)
        if shouldDisable {
            // 復帰条件を満たさない → ゼロのまま、スケーリング無効
            desiredReplicas = 0
            rescale = false
            setCondition(hpa, autoscalingv2.ScalingActive, v1.ConditionFalse, "ScalingDisabled", ...)
        }
    } else if currentReplicas > hpa.Spec.MaxReplicas {
        desiredReplicas = hpa.Spec.MaxReplicas  // 上限超過の即時修正
        needsMetricComputation = false
    } else if currentReplicas < minReplicas {
        desiredReplicas = minReplicas           // 下限割れの即時修正
        needsMetricComputation = false
    }

    // ③ メトリクスからdesiredReplicasを計算（needsMetricComputation=true の場合）
    if needsMetricComputation {
        metricDesiredReplicas, metricName, ..., err = a.computeReplicasForMetrics(...)
        // desiredReplicas の正規化（behavior, stabilization window など）
        desiredReplicas = a.normalizeDesiredReplicasWithBehaviors(...)

        // ゼロから復帰する際、minReplicas 未満にならないよう補正
        if currentReplicas == 0 && minReplicas != 0 && scaledToZeroCondition && desiredReplicas < minReplicas {
            desiredReplicas = minReplicas
        }
        rescale = desiredReplicas != currentReplicas
    }

    // ④ スケール実行
    if rescale {
        scale.Spec.Replicas = desiredReplicas
        _, err := a.scaleNamespacer.Scales(hpa.Namespace).Update(ctx, targetGR, scale, ...)

        // ⑤ ScaledToZero Condition を記録
        if scaleToZeroFeatureEnabled {
            if currentReplicas > 0 && desiredReplicas == 0 && minReplicas == 0 && hasObjectOrExtMetrics {
                // ゼロへスケール完了 → True に設定（次回以降の復帰を可能にする）
                setCondition(hpa, autoscalingv2.ScaledToZero, v1.ConditionTrue, "ScaledToZero", ...)
            } else {
                // 復帰した or ゼロになっていない → False に戻す
                setCondition(hpa, autoscalingv2.ScaledToZero, v1.ConditionFalse, "NotScaledToZero", ...)
            }
        }
    }
}
```

**ポイント:**
- ステップ⑤の Condition 記録が「次のゼロからの復帰」を可能にするトリガー。これを忘れると永久にゼロのまま。
- `desiredReplicas==0` でも `rescale=true` の場合がある（初回のゼロスケール）。
  `rescale=false` になるのは「すでにゼロで、復帰条件も満たさない」ときだけ。

---

## スケール判断のロジックツリー

```
currentReplicas == 0?
├─ YES
│   ├─ canScaleFromZero == true?    （Feature Gate ON + ScaledToZero=True + Object/External あり）
│   │   └─ YES: メトリクス計算 → desiredReplicas 算出 → Scale from Zero 実行
│   ├─ minReplicas != 0 && scaledToZeroCondition?
│   │   └─ YES: メトリクス計算（minReplicas 保証あり）→ Scale from Zero 実行
│   └─ それ以外: rescale=false（ゼロのまま、スケーリング無効）
└─ NO
    ├─ currentReplicas > maxReplicas: desiredReplicas = maxReplicas（即時修正）
    ├─ currentReplicas < minReplicas: desiredReplicas = minReplicas（即時修正）
    └─ それ以外: メトリクス計算 → desiredReplicas 算出
         ├─ desiredReplicas == 0 && minReplicas == 0 && Object/Externalあり
         │   → Scale to Zero 実行 + ScaledToZero=True を記録
         └─ それ以外: 通常スケール
```

---

## データフロー図

```
reconcileAutoscaler()
  │
  ├─[事前条件]─────────────────────────────────────────────────
  │  scaleToZeroFeatureEnabled = Feature Gate チェック
  │  hasObjectOrExtMetrics     = HPA Spec.Metrics をスキャン
  │  scaledToZeroCondition     = Feature Gate ON + HPA Condition ScaledToZero=True
  │  canScaleFromZero          = scaledToZeroCondition + hasObjectOrExtMetrics
  │
  ├─[currentReplicas==0 分岐]──────────────────────────────────
  │  shouldComputeMetricsForZeroReplicas()
  │    → (true, false) : メトリクス計算へ進む
  │    → (false, true) : rescale=false（ゼロのまま）
  │
  ├─[メトリクス計算]────────────────────────────────────────────
  │  computeReplicasForMetrics()
  │    → GetExternalMetricReplicas() → External Metrics API (KEDA が提供)
  │    → desiredReplicas 算出
  │
  ├─[スケール実行]──────────────────────────────────────────────
  │  scale.Update() (Scales subresource)
  │
  └─[Condition 記録]────────────────────────────────────────────
     desiredReplicas==0 → ScaledToZero=True  （次回の復帰を有効化）
     desiredReplicas>0  → ScaledToZero=False （復帰完了を記録）
```

---

## K8s 側の特徴まとめ

| 観点 | 内容 |
|---|---|
| アクティブ判定の場所 | `reconcileAutoscaler()` 内（Scaler 外部） |
| ゼロ判断の根拠 | `desiredReplicas==0`（メトリクス計算の結果） |
| 復帰判断の根拠 | `ScaledToZero Condition=True` + メトリクスが lag>0 を返す |
| CPU/Memory 制限 | `hasObjectOrExternalMetrics()` が false ならゼロスケール不可 |
| 状態の保持先 | HPA オブジェクトの `Status.Conditions`（etcd に永続化） |
| Feature Gate との関係 | Gate OFF なら ScaledToZero Condition を削除、復帰も不可になる |
