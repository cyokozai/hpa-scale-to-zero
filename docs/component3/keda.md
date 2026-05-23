# Component 3: Scale to Zero 実行パス — KEDA v2.16 側

## 概要

KEDA の Scale to Zero は `scaleToZeroOrIdle()` という専用関数が担う。
K8s のように「メトリクス計算結果として 0 が返る」のではなく、
`isActive=false` になった時点で **クールダウン期間のカウントを開始**し、
クールダウンが満了した後に初めて Deployment を 0 にスケールする。

```
checkScalers()
  │
  ├─ getScaledObjectState() → isActive=false
  │
  └─ RequestScale(isActive=false)
       └─ [isActive=false かつ currentReplicas>0 かつ minReplicas=0]
            └─ scaleToZeroOrIdle()
                 ├─ クールダウン未満？ → スキップ（"ScalerCooldown"）
                 └─ クールダウン経過？ → updateScaleOnScaleTarget(replicas=0)
                      └─ scale subresource Update
```

---

## ファイル

| ファイル | 役割 |
|---|---|
| `pkg/scaling/executor/scale_scaledobjects.go` | Scale to Zero / from Zero の実行（専用関数） |
| `pkg/scaling/executor/scale_executor.go` | デフォルト定数（クールダウン 5 分） |

---

## RequestScale — 状態機械のエントリーポイント
### `pkg/scaling/executor/scale_scaledobjects.go:37`

```go
func (e *scaleExecutor) RequestScale(ctx context.Context, scaledObject *kedav1alpha1.ScaledObject,
    isActive bool, isError bool, options *ScaleExecutorOptions) {

    // Deployment/StatefulSet はキャッシュ経由で直接 currentReplicas を取得
    // （API 呼び出し削減のため）
    switch {
    case targetGVKR.Kind == "Deployment":
        e.client.Get(..., deployment)
        currentReplicas = *deployment.Spec.Replicas
    case targetGVKR.Kind == "StatefulSet":
        // 同様
    default:
        currentScale, _ = e.getScaleTargetScale(ctx, scaledObject)
        currentReplicas = currentScale.Spec.Replicas
    }

    // isActive=true の場合
    if isActive {
        switch {
        case currentReplicas == 0:
            e.scaleFromZeroOrIdle(...)   // Scale from Zero（Component 4）
        default:
            e.updateLastActiveTime(...)   // 最終アクティブ時刻を更新
        }
    } else {
        // isActive=false の場合 — Scale to Zero のパス
        switch {
        case currentReplicas > 0 && minReplicas == 0:
            e.scaleToZeroOrIdle(...)     // ← Scale to Zero へ
        case currentReplicas < minReplicas:
            e.updateScaleOnScaleTarget(..., minReplicaCount)  // 下限補正
        default:
            // 変化なし
        }
    }
}
```

**ポイント:**
- `minReplicas == 0` かつ `currentReplicas > 0` のときのみ `scaleToZeroOrIdle()` に進む。
- `isActive=false` になっても **即座にはゼロにしない**。クールダウン判定が間に入る。
- Deployment/StatefulSet はキャッシュから読む最適化がある。

---

## scaleToZeroOrIdle — クールダウンとゼロスケール実行
### `pkg/scaling/executor/scale_scaledobjects.go:247`

```go
// defaultCooldownPeriod = 5 * 60 秒（5 分）
// pkg/scaling/executor/scale_executor.go:37 で定義

func (e *scaleExecutor) scaleToZeroOrIdle(...) {
    // ScaledObject.Spec.CooldownPeriod が設定されていればそちらを優先
    if scaledObject.Spec.CooldownPeriod != nil {
        cooldownPeriod = time.Second * time.Duration(*scaledObject.Spec.CooldownPeriod)
    } else {
        cooldownPeriod = time.Second * time.Duration(defaultCooldownPeriod)  // 300s
    }

    // InitialCooldownPeriod: ScaledObject 作成直後の保護期間
    initialCooldownPeriod := time.Second * time.Duration(scaledObject.Spec.InitialCooldownPeriod)

    // クールダウン判定
    // LastActiveTime が nil = KEDA 管理外でスケールされた → クールダウン無視してゼロへ
    cooldownPassed :=
        (scaledObject.Status.LastActiveTime == nil &&
            scaledObject.ObjectMeta.CreationTimestamp.Add(initialCooldownPeriod).Before(time.Now())) ||
        (scaledObject.Status.LastActiveTime != nil &&
            scaledObject.Status.LastActiveTime.Add(cooldownPeriod).Before(time.Now()))

    if cooldownPassed {
        // クールダウン経過 → ゼロ（または idleReplicaCount）へスケール
        idleValue, scaleToReplicas := getIdleOrMinimumReplicaCount(scaledObject)
        // scaleToReplicas = IdleReplicaCount（設定あり）or MinReplicaCount（= 0）

        currentReplicas, err := e.updateScaleOnScaleTarget(ctx, scaledObject, scale, scaleToReplicas)
        if err == nil {
            // Event: KEDAScaleTargetDeactivated
            e.recorder.Eventf(scaledObject, ..., eventreason.KEDAScaleTargetDeactivated,
                "Deactivated %s %s/%s from %d to %d", ...)
            // ActiveCondition = False
            e.setActiveCondition(..., metav1.ConditionFalse, "ScalerNotActive", ...)
        }
    } else {
        // まだクールダウン中 → ActiveCondition を "ScalerCooldown" に設定
        e.setActiveCondition(..., metav1.ConditionFalse, "ScalerCooldown",
            "Scaler cooling down because triggers are not active")
    }
}
```

**ポイント:**
- クールダウンタイマーの起点は `LastActiveTime`（最後に `isActive=true` だった時刻）。
  lag が 0 になって `isActive=false` になった瞬間ではなく、
  **最後に lag > 0 だったときの時刻**からカウントが始まる。
- `IdleReplicaCount` が設定されている場合は 0 ではなく idle 数にスケール（"アイドル" モード）。
- `LastActiveTime=nil` は KEDA 外部から Deployment がスケールされたケース。
  この場合は `CreationTimestamp + InitialCooldownPeriod` を基準にする。

---

## updateScaleOnScaleTarget — scale subresource による実際の更新
### `pkg/scaling/executor/scale_scaledobjects.go:335`

```go
func (e *scaleExecutor) updateScaleOnScaleTarget(ctx context.Context,
    scaledObject *kedav1alpha1.ScaledObject, scale *autoscalingv1.Scale, replicas int32) (int32, error) {

    if scale == nil {
        scale, _ = e.getScaleTargetScale(ctx, scaledObject)
    }

    currentReplicas := scale.Spec.Replicas
    scale.Spec.Replicas = replicas  // = 0

    // K8s の scale subresource API を呼ぶ（HPA が使うのと同じ API）
    _, err := e.scaleClient.Scales(scaledObject.Namespace).Update(
        ctx, scaledObject.Status.ScaleTargetGVKR.GroupResource(), scale, metav1.UpdateOptions{})
    return currentReplicas, err
}
```

**ポイント:**
- K8s の `scale subresource`（`/scale`）を使う。K8s HPA の `scale.Update()` と**同じ API**。
- ただし呼び出し元が KEDA のスケールループなので、HPA を経由せずに直接 Deployment を 0 にする。
  K8s HPA も同じ Deployment を管理しているが、`minReplicas=0` の HPA は
  `shouldComputeMetricsForZeroReplicas()` で計算をスキップするため競合しない。

---

## Scale to Zero の全体シーケンス（KEDA）

```
[前提条件]
  - ScaledObject spec.minReplicaCount = 0
  - Kafka consumer group lag > 0 → lag = 0 に変化

[KEDA polling period: ScaledObject.spec.pollingInterval, デフォルト 30s]

checkScalers()
  │
  ├─ kafka_scaler.GetMetricsAndActivity()
  │    lag=0 → totalLagWithPersistent=0 → isActive=false
  │
  └─ RequestScale(isActive=false)
       │
       └─ [isActive=false && currentReplicas>0 && minReplicas=0]
            └─ scaleToZeroOrIdle()
                 │
                 ├─ LastActiveTime=T (lag=0 になった時刻ではなく最後にactive=trueだった時刻)
                 ├─ cooldownPeriod=300s（デフォルト）
                 │
                 ├─ T + 300s > now ？ → クールダウン中
                 │    └─ "ScalerCooldown" を ActiveCondition に記録してスキップ
                 │
                 └─ T + 300s < now ？ → クールダウン完了
                      └─ updateScaleOnScaleTarget(replicas=0)
                           └─ scale subresource Update
                                → Deployment.Spec.Replicas = 0
                                → Event: KEDAScaleTargetDeactivated
                                → ActiveCondition = False("ScalerNotActive")
```

---

## クールダウンの意義と K8s との差異

KEDA がクールダウンを持つ理由:

Kafka の lag は一時的にゼロになることがある（burst 消費後の谷間など）。
クールダウンなしでは lag=0 の瞬間に Pod が落ちて、すぐ lag が戻って再起動という
スラッシング（ちらつき）が発生する。

```
クールダウンなし（危険）:
  lag: 100 → 0 → 50 → 0 → 30
  Pod:  N  → 0 →  1 → 0 →  1  ← 頻繁な再起動でコストと遅延が増大

クールダウンあり（デフォルト 5 分）:
  lag: 100 → 0 → 50 → 0 → 0 → 0 → 0 → 0 → 0 → 0（5 分間継続）
  Pod:  N  → N → N  → N → N → N → N → N → N → 0  ← 安定後にゼロへ
```

K8s の HPA は `behavior.scaleDown.stabilizationWindowSeconds`（デフォルト 300s）で同等の保護を提供するが、
それは「直近 N 秒の中の最大 desiredReplicas」を使う方式であり、KEDA の明示的なクールダウンとは設計思想が異なる。

---

## KEDA 側の Scale to Zero まとめ

| 観点 | 内容 |
|---|---|
| トリガー条件 | `isActive=false` かつ `currentReplicas>0` かつ `minReplicas=0` |
| クールダウン | デフォルト **300 秒**（`spec.cooldownPeriod` で変更可） |
| クールダウン基点 | `LastActiveTime`（最後に active=true だった時刻） |
| ゼロへのスケール | scale subresource Update（K8s HPA と同じ API） |
| 状態記録 | `ActiveCondition=False` + `KEDAScaleTargetDeactivated` イベント |
| Condition との違い | HPA の `ScaledToZero Condition` は記録しない（KEDA が復帰を独自管理するため） |
