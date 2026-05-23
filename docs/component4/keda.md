# Component 4: Scale from Zero 実行パス — KEDA v2.16 側

## 概要

KEDA の Scale from Zero は `scaleFromZeroOrIdle()` が担う。
`isActive=true` かつ `currentReplicas==0` を検知した **その同じポーリング周期内**で
Deployment を `max(minReplicaCount, 1)` にスケールアップする。
クールダウンも待たず、Condition のチェックも不要。
その後の継続的なスケールアップ（例: 1→5 Pod）は KEDA が生成した HPA が引き継ぐ。

```
checkScalers() [isActive=true, currentReplicas=0]
  │
  ├─ [1] getScaledObjectState() → isActive=true
  │       kafka_scaler: lag > activationLagThreshold → true
  │
  └─ [2] RequestScale(isActive=true)
       └─ [currentReplicas=0]
            └─ [3] scaleFromZeroOrIdle()
                    replicas = max(minReplicaCount, 1)
                    └─ [4] updateScaleOnScaleTarget(replicas)
                            scale subresource Update
                            LastActiveTime = now
                            Event: KEDAScaleTargetActivated
```

---

## ファイル

| ファイル | 役割 |
|---|---|
| `pkg/scaling/executor/scale_scaledobjects.go` | Scale from Zero 実行（`scaleFromZeroOrIdle()`） |

---

## [1] isActive=true の判断
### `keda-2.16/pkg/scalers/kafka_scaler.go:930`（Component 1 で解析済み）

```go
func (s *kafkaScaler) GetMetricsAndActivity(...) (..., bool, error) {
    totalLag, totalLagWithPersistent, _ := s.getTotalLag()
    // activationLagThreshold デフォルト=0
    // lag > 0 になった瞬間に isActive=true を返す
    return metrics, totalLagWithPersistent > s.metadata.activationLagThreshold, nil
}
```

**ポイント:**
- `activationLagThreshold`（デフォルト 0）を超えた瞬間に `isActive=true`。
  K8s が「次のメトリクス計算で desiredReplicas > 0」を待つのとは異なり、
  **bool フラグが変わった最初のポーリングで即座に復帰処理が動く**。
- `totalLagWithPersistent` を使うため、`excludePersistentLag=true` の場合でも
  persistent lag があれば復帰できる（Component 1 参照）。

---

## [2] RequestScale — isActive=true かつ currentReplicas=0 の分岐
### `pkg/scaling/executor/scale_scaledobjects.go:125`

```go
if isActive {
    switch {
    case scaledObject.Spec.IdleReplicaCount != nil && currentReplicas < minReplicas,
        currentReplicas == 0:
        // isActive=true かつ (IdleMode で下限割れ or ゼロ) → Scale from Zero
        e.scaleFromZeroOrIdle(ctx, logger, scaledObject, currentScale, options.ActiveTriggers)
    case isError:
        // エラーがあるが一部は active → ReadyCondition を Unknown に設定
        e.setReadyCondition(..., metav1.ConditionUnknown, ...)
    default:
        // 通常稼働中（currentReplicas > 0）→ LastActiveTime だけ更新
        e.updateLastActiveTime(ctx, logger, scaledObject)
    }
}
```

**ポイント:**
- `currentReplicas == 0` の case が `IdleReplicaCount` のケースと同じ switch にまとまっている。
  IdleReplicaCount（アイドル設定）の場合も同じ `scaleFromZeroOrIdle()` で処理する。

---

## [3] scaleFromZeroOrIdle — 実際のスケールアップ
### `pkg/scaling/executor/scale_scaledobjects.go:305`

```go
func (e *scaleExecutor) scaleFromZeroOrIdle(ctx context.Context, logger logr.Logger,
    scaledObject *kedav1alpha1.ScaledObject, scale *autoscalingv1.Scale, activeTriggers []string) {

    // 復帰先レプリカ数の決定
    var replicas int32
    if scaledObject.Spec.MinReplicaCount != nil && *scaledObject.Spec.MinReplicaCount > 0 {
        replicas = *scaledObject.Spec.MinReplicaCount  // 設定値（例: 2）
    } else {
        replicas = 1  // 未設定 or 0 の場合は最低 1
    }
    // ← メトリクス値（lag の大きさ）は参照しない

    // scale subresource 経由でスケール
    currentReplicas, err := e.updateScaleOnScaleTarget(ctx, scaledObject, scale, replicas)

    if err == nil {
        logger.Info("Successfully updated ScaleTarget",
            "Original Replicas Count", currentReplicas,
            "New Replicas Count", replicas)

        // Event 発行: どのトリガーが復帰を引き起こしたかを記録
        e.recorder.Eventf(scaledObject, corev1.EventTypeNormal,
            eventreason.KEDAScaleTargetActivated,
            "Scaled %s %s/%s from %d to %d, triggered by %s",
            scaledObject.Status.ScaleTargetKind, scaledObject.Namespace,
            scaledObject.Spec.ScaleTargetRef.Name,
            currentReplicas, replicas,
            strings.Join(activeTriggers, ";"))  // 例: "kafka-trigger"

        // LastActiveTime を更新（クールダウンのタイマーリセット）
        e.updateLastActiveTime(ctx, logger, scaledObject)
    }
}
```

**ポイント:**
- 復帰先は常に `max(minReplicaCount, 1)` で固定。lag の大きさは関係しない。
  これが K8s の `ceil(lag / targetUsage)` と根本的に異なる点。
- `activeTriggers` をイベントに記録するため、複数トリガーのうちどれが復帰を引き起こしたか追跡できる。
- 復帰直後に `LastActiveTime` を更新することで、次のクールダウンの起点をリセットする。

---

## 2ステップスケールアップの仕組み

KEDA の Scale from Zero は「KEDA が minReplicas まで復帰 → HPA が追加スケール」の 2 段階で行われる。

```
ステップ1: KEDA が scaleFromZeroOrIdle() を実行
  Deployment: 0 → minReplicaCount (例: 1)
  ↓
  Pod が起動し、consumer として動き始める

ステップ2: K8s HPA が通常スケールを継続
  HPA の計算: lag=500, target=100, currentReplicas=1
  usageRatio = 500 / 100 = 5.0
  desiredReplicas = ceil(5.0 * readyPodCount=1) = 5
  Deployment: 1 → 5
```

**なぜ 2 段階か:**
- ゼロの状態では `getReadyPodsCount()` が 0 を返し、HPA は `desiredReplicas=ceil(5.0 * 0)=0` を計算してしまう。
  HPA 単独では `currentReplicas=0` から復帰できない（K8s の Scale from Zero feature なしでは）。
- KEDA が 1 Pod 起動することで HPA が機能できる状態を作る。

---

## Scale from Zero の全体シーケンス（KEDA）

```
[前提条件]
  - ScaledObject spec.minReplicaCount = 0
  - currentReplicas = 0（scaleToZeroOrIdle() によって到達）
  - Kafka に新しいメッセージが届き始める

[KEDA polling period: ScaledObject.spec.pollingInterval, デフォルト 30s]

checkScalers()
  │
  ├─ kafka_scaler.GetMetricsAndActivity()
  │    lag=50 > activationLagThreshold=0 → isActive=true
  │
  └─ RequestScale(isActive=true)
       │
       └─ [isActive=true && currentReplicas=0]
            └─ scaleFromZeroOrIdle()
                 replicas = max(minReplicaCount=0, 1) = 1
                 updateScaleOnScaleTarget(replicas=1)
                   → Deployment.Spec.Replicas = 1
                   → Event: KEDAScaleTargetActivated ("triggered by kafka-trigger")
                 updateLastActiveTime()
                   → LastActiveTime = now

[HPA が引き継ぐ]
  HPA sync 周期（デフォルト 15s）
  lag=50, target=10, currentReplicas=1 (readyPods=1)
  desiredReplicas = ceil(5.0 * 1) = 5
  Deployment: 1 → 5
```

---

## KEDA 側の Scale from Zero まとめ

| 観点 | 内容 |
|---|---|
| トリガー条件 | `isActive=true`（`totalLagWithPersistent > activationLagThreshold`） かつ `currentReplicas=0` |
| 復帰レプリカ数 | `max(minReplicaCount, 1)` — lag の大きさは無関係 |
| Condition 依存 | なし（`ScaledToZero Condition` を参照しない） |
| クールダウン待ち | なし（`isActive=true` になった瞬間に実行） |
| イベント記録 | `KEDAScaleTargetActivated`（どのトリガーが発火したか含む） |
| 継続スケール | HPA が引き継ぐ（KEDA は 2 回目以降の `currentReplicas>0` 状態のスケールを行わない） |
| 状態更新 | `LastActiveTime = now`（クールダウンタイマーのリセット） |
