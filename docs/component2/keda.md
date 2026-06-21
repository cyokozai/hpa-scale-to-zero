# Component 2: スケール判断ロジック — KEDA v2.16 側

## 概要

KEDA のスケール判断は **独自のポーリングループ**が担う。
`ScaledObjectReconciler` が ScaledObject を監視して HPA を生成・管理しつつスケールループを起動し、
`scaleHandler.checkScalers()` が定期的に全 Scaler の `isActive` を集約して
`RequestScale()` に渡す。K8s HPA の `reconcileAutoscaler()` とは異なり、
**ゼロへのスケールはこのループが Deployment を直接操作する**（Component 3 で詳細）。

```
ScaledObjectReconciler.Reconcile()
  └─ requestScaleLoop() → ScaleHandler.HandleScalableObject()
       └─ [goroutine] scaleLoop()
            └─ checkScalers()         ← ポーリング周期ごとに実行
                 ├─ getScaledObjectState()    ← 全 Scaler の isActive を集約
                 │    └─ [goroutine × N] getScalerState()
                 │         └─ Scaler.GetMetricsAndActivity()  ← Component 1
                 └─ scaleExecutor.RequestScale()   ← スケール実行へ
```

---

## ファイル

| ファイル | 役割 |
|---|---|
| `controllers/keda/scaledobject_controller.go` | ScaledObject の reconciler（HPA 管理・スケールループ起動） |
| `pkg/scaling/scale_handler.go` | スケールループ本体・全 Scaler の状態集約 |

---

## ScaledObjectReconciler.Reconcile — HPA 管理とループ起動
### `controllers/keda/scaledobject_controller.go:151`

```go
func (r *ScaledObjectReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    scaledObject := &kedav1alpha1.ScaledObject{}
    _ = r.Client.Get(ctx, req.NamespacedName, scaledObject)

    // 削除タイムスタンプがある場合はファイナライザー処理（スケールループ停止）
    if scaledObject.GetDeletionTimestamp() != nil {
        return ctrl.Result{}, r.finalizeScaledObject(...)
    }

    // Status Conditions の初期化（初回のみ）
    if !scaledObject.Status.Conditions.AreInitialized() {
        conditions := kedav1alpha1.GetInitializedConditions()
        kedastatus.SetStatusConditions(...)
    }

    // ScaledObject の内容を検証・HPA を作成/更新し、スケールループを起動する
    msg, err := r.reconcileScaledObject(ctx, reqLogger, scaledObject, &conditions)
    // ...
}
```

**ポイント:**
- `reconcileScaledObject()` の中で HPA リソースを作成/更新し、`requestScaleLoop()` を呼ぶ。
- K8s の HPA controller とは別に KEDA 独自のループが存在する。両者が共存する構造。

---

## requestScaleLoop — スケールループの起動
### `controllers/keda/scaledobject_controller.go:521`

```go
// ScaledObject に対するスケールループを開始する。
// HandleScalableObject が goroutine でループを走らせる。
func (r *ScaledObjectReconciler) requestScaleLoop(ctx context.Context, logger logr.Logger, scaledObject *kedav1alpha1.ScaledObject) error {
    // Generation が変わっていない場合はループを再起動しない
    key, _ := cache.MetaNamespaceKeyFunc(scaledObject)

    if err = r.ScaleHandler.HandleScalableObject(ctx, scaledObject); err != nil {
        return err
    }

    // 現在の Generation を記録（次回 Reconcile でループ再起動が必要か判断するため）
    r.scaledObjectsGenerations.Store(key, scaledObject.Generation)
    return nil
}
```

**ポイント:**
- `scaledObjectsGenerations` で Generation を追跡。`Spec` が変わっていない限りループを無駄に再起動しない。
- `HandleScalableObject()` が実際に goroutine を起動し、その中で `checkScalers()` をポーリング周期で呼ぶ。

---

## getScaledObjectState — 全 Scaler のアクティブ状態を集約
### `pkg/scaling/scale_handler.go:598`

```go
// isScaledObjectActive: 少なくとも1つの Scaler が active → true
// isScaledObjectError: いずれかの Scaler でエラー
// metricsRecord: メトリクスキャッシュ更新用
func (h *scaleHandler) getScaledObjectState(ctx context.Context, scaledObject *kedav1alpha1.ScaledObject) (bool, bool, map[string]metricscache.MetricsRecord, []string, error) {
    // CPU/Memory トリガーの数を事前カウント（Scale to Zero 制限の判断に使う）
    cpuMemCount := 0
    for _, trigger := range scaledObject.Spec.Triggers {
        if trigger.Type == "cpu" || trigger.Type == "memory" {
            cpuMemCount++
        }
    }

    // 全 Scaler を goroutine で並行実行
    allScalers, scalerConfigs := cache.GetScalers()
    results := make(chan scalerState, len(allScalers))
    wg := sync.WaitGroup{}
    for scalerIndex := 0; scalerIndex < len(allScalers); scalerIndex++ {
        wg.Add(1)
        go func(scaler scalers.Scaler, ...) {
            results <- h.getScalerState(ctx, scaler, ...)  // GetMetricsAndActivity() を呼ぶ
            wg.Done()
        }(allScalers[scalerIndex], ...)
    }
    wg.Wait()
    close(results)

    // いずれか1つでも isActive=true なら ScaledObject 全体を active とする
    for result := range results {
        if result.IsActive {
            isScaledObjectActive = true
            activeTriggers = append(activeTriggers, result.TriggerName)
        }
        if result.Err != nil {
            isScaledObjectError = true
        }
    }

    // ScalingModifiers（合成メトリクス）を使っている場合は active を再評価
    matchingMetrics = modifiers.HandleScalingModifiers(...)
    if scaledObject.IsUsingModifiers() { /* 再評価ロジック */ }

    // CPU/Memory のみのトリガー構成は Scale to Zero 不可
    // → 永久に active=true を強制して HPA が最低 minReplicas を維持する
    if len(scaledObject.Spec.Triggers) <= cpuMemCount && !isScaledObjectError {
        isScaledObjectActive = true
    }

    return isScaledObjectActive, isScaledObjectError, metricsRecord, activeTriggers, err
}
```

**ポイント:**
- 全 Scaler を **goroutine で並行実行**して最初に true を返したものが勝つ OR 論理。
- CPU/Memory のみのトリガーは `isActive=true` に強制。K8s の `hasObjectOrExternalMetrics()` と同じ制約を
  KEDA 側でも持っている（CPU/Memory だけでは lag=0 になっても復帰できないため）。
- エラーが出た場合は `scalerCaches` をクリアし、次回呼び出しで再構築する。

---

## checkScalers — ポーリングループの中心
### `pkg/scaling/scale_handler.go:231`

```go
// ScaleHandler のポーリング周期ごとに呼ばれるメイン関数。
// アクティブ状態を判断して RequestScale に渡す。
func (h *scaleHandler) checkScalers(ctx context.Context, scalableObject interface{}, scalingMutex sync.Locker) {
    scalingMutex.Lock()
    defer scalingMutex.Unlock()
    switch obj := scalableObject.(type) {
    case *kedav1alpha1.ScaledObject:
        // 最新の ScaledObject を API server から取得（キャッシュ更新）
        h.client.Get(ctx, types.NamespacedName{...}, obj)

        // 全 Scaler の状態を集約
        isActive, isError, metricsRecords, activeTriggers, err := h.getScaledObjectState(ctx, obj)

        // スケール実行（Component 3/4 で詳細）
        h.scaleExecutor.RequestScale(ctx, obj, isActive, isError,
            &executor.ScaleExecutorOptions{ActiveTriggers: activeTriggers})

        // メトリクス値をキャッシュに保存（KEDA の metrics-apiserver が K8s HPA に提供するため）
        h.scaledObjectsMetricCache.StoreRecords(obj.GenerateIdentifier(), metricsRecords)
    }
}
```

**ポイント:**
- `scalingMutex` で排他制御。同一 ScaledObject への同時スケール判断を防ぐ。
- `metricsRecords` をキャッシュに保存することで、K8s HPA が External Metrics API 経由で値を取得できる。
  つまり **KEDA のスケールループ（独自制御）** と **K8s HPA ループ（通常スケール）** の2系統が並行動作する。

---

## スケール判断のロジックツリー（KEDA）

```
checkScalers() [ポーリング周期ごと]
  │
  ├─ getScaledObjectState()
  │    ├─ Scaler 1: GetMetricsAndActivity() → (metrics, isActive=true/false, err)
  │    ├─ Scaler 2: ...
  │    └─ OR 論理: いずれか1つでも active → isScaledObjectActive=true
  │         ※ CPU/Memory のみ構成 → 強制 active=true
  │
  └─ RequestScale(ctx, scaledObject, isActive, isError)
       ├─ isActive=true  && currentReplicas==0 → Scale from Zero（Deployment 直接操作）
       ├─ isActive=false && currentReplicas>0  && minReplicas==0 → Scale to Zero（Deployment 直接操作）
       └─ それ以外 → HPA の target を更新（通常スケール）
```

---

## データフロー図

```
ScaledObjectReconciler.Reconcile()
  │
  ├─ HPA を作成/更新（K8s API）
  └─ requestScaleLoop() → HandleScalableObject()
       │
       └─ [goroutine: ポーリングループ]
            └─ checkScalers() ─────────────────────────────────────────
                 │
                 ├─ getScaledObjectState()
                 │    └─ [goroutine × N Scalers]
                 │         └─ getScalerState()
                 │              └─ kafka_scaler.GetMetricsAndActivity()
                 │                   → (metrics, isActive bool, err)
                 │         ↓ OR 集約
                 │    isScaledObjectActive (bool)
                 │
                 ├─ scaledObjectsMetricCache.StoreRecords()
                 │    → K8s HPA の External Metrics API 経由で参照される
                 │
                 └─ scaleExecutor.RequestScale(isActive)
                      → Component 3/4: Scale to/from Zero 実行
```

---

## KEDA 側の特徴まとめ

| 観点 | 内容 |
|---|---|
| アクティブ判定の場所 | `checkScalers()` → `getScaledObjectState()`（Scaler の `isActive` を集約） |
| ゼロ判断の根拠 | `isActive=false`（Scaler が返す bool） |
| 復帰判断の根拠 | `isActive=true` になった瞬間（Condition 不要） |
| CPU/Memory 制限 | `cpuMemCount == len(Triggers)` のとき強制 `isActive=true` |
| 状態の保持先 | ScaledObject の Status（+ `isActive` は毎回 Scaler が再計算） |
| K8s HPA との関係 | 2系統が並行: KEDA ループ（ゼロ制御）+ K8s HPA ループ（通常スケール） |
