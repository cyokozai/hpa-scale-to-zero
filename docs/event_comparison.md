# イベント比較: KEDA v2.16 vs K8s HPA v1.36

## 概要

KEDA v2.16 と Kubernetes HPA v1.36（`HPAScaleToZero` Feature Gate 有効）が
Scale to Zero / Scale from Zero 時に発行するイベント・Condition を網羅的に比較する。

- **KEDA 定義元**: `pkg/eventreason/eventreason.go`（25 定数）
- **K8s 定義元**: `pkg/controller/podautoscaler/horizontal.go`（14 Event + HPAScaleToZero 固有 Condition）

設計上の本質的な違い:
- **KEDA**: Scale to Zero / from Zero の局面を専用イベント（`KEDAScaleTargetDeactivated` / `KEDAScaleTargetActivated`）で明示的に発行する
- **K8s HPA**: Scale to Zero は `SuccessfulRescale` で通知し、ゼロ状態の維持は Condition（`ScaledToZero=True`）で管理する。Event と Condition が役割分担する設計

---

## 主軸比較: Scale to Zero / from Zero フェーズ

| 局面 | KEDA v2.16 | K8s HPA v1.36 |
|---|---|---|
| **Scale to Zero 完了** | `KEDAScaleTargetDeactivated`（Normal）| `SuccessfulRescale`（Normal, "New size: 0"）|
| **ゼロ状態の記録** | ScaledObject `.status.lastActiveTime` | HPA Condition `ScaledToZero=True` |
| **Scale from Zero 完了** | `KEDAScaleTargetActivated`（Normal）+ HPA `SuccessfulRescale` | HPA `SuccessfulRescale`（Normal, "New size: N"）|
| **Scale to Zero 失敗** | `KEDAScaleTargetDeactivationFailed`（Warning）| `FailedRescale`（Warning）|
| **Scale from Zero 失敗** | `KEDAScaleTargetActivationFailed`（Warning）| `FailedRescale`（Warning）|
| **メトリクス取得失敗** | `KEDAScalerFailed` / `KEDAMetricSourceFailed`（Warning）| `FailedGetExternalMetric` 等（Warning）|
| **メトリクス計算失敗** | `KEDAScalerFailed`（Warning）| `FailedComputeMetricsReplicas`（Warning）|

### Scale from Zero の発行イベント数の違い

KEDA の Scale from Zero は **2 イベント**が発行される（0→1 と 1→N が別フェーズ）。
K8s HPA の Scale from Zero は **1 イベント**（0→N を 1 ステップで実行）。

```
KEDA:
  1. KEDAScaleTargetActivated  "Scaled ... from 0 to 1, triggered by kafkaScaler"
  2. SuccessfulRescale         "New size: 3; reason: external metric ... above target"

K8s HPA:
  1. SuccessfulRescale         "New size: 3; reason: external metric ... above target"
```

実測（2026-06-03）では 1 と 2 が **同一秒内**に完結し、監視間隔（3s）では 1 ステップに見えたが
`kubectl get events` でシーケンスを確認できた（`verification.md` 参照）。

---

## KEDA v2.16 全イベント一覧

`pkg/eventreason/eventreason.go` に定義された 25 定数を 5 カテゴリに分類する。

### カテゴリ 1: Scale Target（Scale to Zero / from Zero の主役）

| 定数名 | 値 | Type | 発行タイミング |
|---|---|---|---|
| `KEDAScaleTargetActivated` | `"KEDAScaleTargetActivated"` | Normal | `scaleFromZeroOrIdle()` 完了時（0→1 ステップ）|
| `KEDAScaleTargetDeactivated` | `"KEDAScaleTargetDeactivated"` | Normal | `scaleToZeroOrIdle()` 完了時（N→0 ステップ）|
| `KEDAScaleTargetActivationFailed` | `"KEDAScaleTargetActivationFailed"` | Warning | Scale from Zero 失敗時 |
| `KEDAScaleTargetDeactivationFailed` | `"KEDAScaleTargetDeactivationFailed"` | Warning | Scale to Zero 失敗時 |

実装参照: `pkg/scaling/executor/scale_scaledobjects.go`

### カテゴリ 2: Scaler（メトリクス収集・スケーラー制御）

| 定数名 | 値 | Type | 発行タイミング |
|---|---|---|---|
| `KEDAScalersStarted` | `"KEDAScalersStarted"` | Normal | ScaledObject の Scaler 監視ループ開始時 |
| `KEDAScalersStopped` | `"KEDAScalersStopped"` | Normal | Scaler 監視ループ停止時（ScaledObject 削除等）|
| `KEDAScalerFailed` | `"KEDAScalerFailed"` | Warning | 個別 Scaler のエラー時（Kafka 接続失敗等）|
| `KEDAMetricSourceFailed` | `"KEDAMetricSourceFailed"` | Warning | カスタム数式のメトリクスソースエラー時 |

実装参照: `pkg/scaling/scale_handler.go`

### カテゴリ 3: ScaledObject / ScaledJob ライフサイクル

| 定数名 | 値 | Type | 発行タイミング |
|---|---|---|---|
| `ScaledObjectReady` | `"ScaledObjectReady"` | Normal | ScaledObject の検証・準備完了時 |
| `ScaledObjectCheckFailed` | `"ScaledObjectCheckFailed"` | Warning | ScaledObject validation 失敗時 |
| `ScaledObjectUpdateFailed` | `"ScaledObjectUpdateFailed"` | Warning | ScaledObject Status 更新失敗時 |
| `ScaledObjectDeleted` | `"ScaledObjectDeleted"` | Normal | ScaledObject 削除時 |
| `ScaledJobReady` | `"ScaledJobReady"` | Normal | ScaledJob の準備完了時 |
| `ScaledJobCheckFailed` | `"ScaledJobCheckFailed"` | Warning | ScaledJob validation 失敗時 |
| `ScaledJobUpdateFailed` | `"ScaledJobUpdateFailed"` | Warning | ScaledJob Status 更新失敗時 |
| `ScaledJobDeleted` | `"ScaledJobDeleted"` | Normal | ScaledJob 削除時 |
| `KEDAJobsCreated` | `"KEDAJobsCreated"` | Normal | ScaledJob によるジョブ作成時 |

実装参照: `controllers/keda/scaledobject_controller.go`

### カテゴリ 4: TriggerAuthentication

| 定数名 | 値 | Type | 発行タイミング |
|---|---|---|---|
| `TriggerAuthenticationAdded` | `"TriggerAuthenticationAdded"` | Normal | TriggerAuthentication 追加時 |
| `TriggerAuthenticationUpdated` | `"ClusterTriggerAuthenticationUpdated"` | Normal | TriggerAuthentication 更新時 ※1 |
| `TriggerAuthenticationFailed` | `"TriggerAuthenticationFailed"` | Warning | TriggerAuthentication エラー時 |
| `TriggerAuthenticationDeleted` | `"TriggerAuthenticationDeleted"` | Normal | TriggerAuthentication 削除時 |

### カテゴリ 5: ClusterTriggerAuthentication

| 定数名 | 値 | Type | 発行タイミング |
|---|---|---|---|
| `ClusterTriggerAuthenticationAdded` | `"ClusterTriggerAuthenticationAdded"` | Normal | ClusterTriggerAuthentication 追加時 |
| `ClusterTriggerAuthenticationUpdated` | `"ClusterTriggerAuthenticationUpdated"` | Normal | ClusterTriggerAuthentication 更新時 |
| `ClusterTriggerAuthenticationFailed` | `"ClusterTriggerAuthenticationFailed"` | Warning | ClusterTriggerAuthentication エラー時 |
| `ClusterTriggerAuthenticationDeleted` | `"ClusterTriggerAuthenticationDeleted"` | Normal | ClusterTriggerAuthentication 削除時 |

> **※1 コード上のバグ**: `TriggerAuthenticationUpdated` の値が `"ClusterTriggerAuthenticationUpdated"` になっており、
> `ClusterTriggerAuthenticationUpdated` と同一の文字列が使われている（`eventreason.go:81`）。

---

## K8s HPA v1.36 全イベント一覧

`horizontal.go` の `eventRecorder.Event()` 呼び出しから抽出した 14 種類のイベント。

### Normal イベント（1 種類）

| reason | 発行タイミング | 備考 |
|---|---|---|
| `SuccessfulRescale` | スケール成功時（Line 979）| "New size: N; reason: ..." の形式。Scale to Zero も Scale from Zero も同一 reason で表現する |

### Warning イベント（13 種類）

| reason | 分類 | 発行タイミング | コード位置 |
|---|---|---|---|
| `SelectorRequired` | Selector | Scale ターゲットに Selector がない | L400 |
| `InvalidSelector` | Selector | Selector のパース失敗 | L408 |
| `AmbiguousSelector` | Selector | 複数 HPA が同じ Selector を対象 | L429 |
| `FailedGetScale` | スケール取得 | ターゲットの現在 Scale 取得失敗 | L798,813,823 |
| `FailedComputeMetricsReplicas` | メトリクス | レプリカ数計算全体の失敗 | L895 |
| `FailedGetObjectMetric` | メトリクス | Object メトリクス取得失敗 | L470,560,569,578 |
| `FailedGetPodsMetric` | メトリクス | Pods メトリクス取得失敗 | L480,587 |
| `FailedGetResourceMetric` | メトリクス | Resource メトリクス取得失敗 | L669 |
| `FailedGetContainerResourceMetric` | メトリクス | ContainerResource メトリクス取得失敗 | L688 |
| `FailedGetExternalMetric` | メトリクス | External メトリクス取得失敗（今回の検証で観測）| L708,728,747 |
| `InvalidMetricSourceType` | メトリクス | 未知のメトリクスソース種別 | L505 |
| `FailedRescale` | スケール実行 | scale subresource への書き込み失敗 | L968 |
| `FailedUpdateStatus` | Status 更新 | HPA Status の更新失敗 | L1503 |

> `FailedGet*` は `getUnableComputeReplicaCountCondition()` 経由で Event と Condition の両方を同時に記録する（`horizontal.go:1143-1151`）。

### HPAScaleToZero 固有: Condition による状態管理

K8s HPA は Scale to Zero の状態を **Event ではなく Condition** で管理する。
`ScaledToZero` Condition はゼロ状態の「待機フラグ」として機能し、ゼロ状態でもメトリクス計算を継続させる。

```go
// horizontal.go:990 — Scale to Zero 実行直後に Condition を付与
setCondition(hpa, autoscalingv2.ScaledToZero, v1.ConditionTrue,
    "ScaledToZero", "the HPA controller scaled the workload to zero")
```

| Condition Type | Status | Reason | 意味 |
|---|---|---|---|
| `ScaledToZero` | True | `ScaledToZero` | 0 レプリカ状態。`canScaleFromZero=true` が有効になりメトリクス計算継続 |
| `ScaledToZero` | False | `NotScaledToZero` | Scale from Zero 後にクリアされる |
| `AbleToScale` | True | `ScaleDownStabilized` | stabilization window で保留中 |
| `AbleToScale` | True | `ReadyForNewScale` | スケール可能な安定状態 |
| `AbleToScale` | True | `SucceededRescale` | スケール命令発行直後（Pod 終了前）|
| `ScalingActive` | False | `FailedGetExternalMetric` | メトリクス取得失敗（フェイルセーフ: 現状維持）|
| `ScalingLimited` | True | `ScaleUpLimit` | scaleUp ポリシーで上限制限中 |

---

## 設計思想の比較

### KEDA: Scale to Zero を「ファーストクラスのイベント」として扱う

KEDA は 0→N（Activation）と N→0（Deactivation）を通常スケールと明確に分離し、
専用の Event reason を定義している。

```
通常スケール:  HPA の SuccessfulRescale に任せる（KEDA は Event を発行しない）
Activation:   KEDAScaleTargetActivated（0→1）で KEDA の関与を明示
Deactivation: KEDAScaleTargetDeactivated（N→0）で KEDA の関与を明示
```

この設計により `kubectl get events --field-selector reason=KEDAScaleTargetDeactivated` で
Scale to Zero の発生履歴だけを抽出できる。

### K8s HPA: Event は「結果」、Condition は「状態」という役割分担

```
SuccessfulRescale（Event）: スケールという「動作」を記録（size=0 も size=N も同一 reason）
ScaledToZero=True（Condition）: ゼロという「状態」を永続的に保持（Scale from Zero まで維持）
```

Scale to Zero を区別したい場合は Event の message "New size: 0" を文字列検索するか、
`ScaledToZero=True` Condition の付与タイミングを見る必要がある。
一方、`ScaledToZero=True` Condition は「ゼロ状態でもメトリクス計算を継続する」
（`shouldComputeMetricsForZeroReplicas()` の `canScaleFromZero=true`）という
動作フラグとしても機能しており、Event では表現できない役割を担っている。

### イベント粒度の比較

| 観点 | KEDA v2.16 | K8s HPA v1.36 |
|---|---|---|
| Scale to Zero 専用 Event | あり（`KEDAScaleTargetDeactivated`）| なし（`SuccessfulRescale` で代替）|
| Scale from Zero 専用 Event | あり（`KEDAScaleTargetActivated`）| なし（`SuccessfulRescale` で代替）|
| ゼロ状態の永続記録 | ScaledObject Status `lastActiveTime` | HPA Condition `ScaledToZero=True` |
| メトリクス取得失敗の粒度 | Scaler 単位（`KEDAScalerFailed`）| メトリクス種別単位（5 種類）|
| 認証エラーの粒度 | 8 種類（TriggerAuth / ClusterTriggerAuth × 4 操作）| なし（HPA は認証情報を直接持たない）|
| Job スケール | 3 種類（ScaledJobReady / CheckFailed / KEDAJobsCreated）| なし（HPA は Job 非対応）|

---

## 実測との対応

実測は `verification.md` に記録済み。

### 2026-06-03 KEDA テストラン（Kafka Consumer Scale to Zero / from Zero）

```
観測イベント                       発生時刻     イベント reason
─────────────────────────────────────────────────────────────────
Scale from Zero（0→1→3）:
  KEDAScaleTargetActivated        20:10:36    "Scaled ... from 0 to 1, triggered by kafkaScaler"
  SuccessfulRescale               20:10:36    "New size: 3; reason: external metric ... above target"
  ↑ 同一秒内に 2 ステップ完了（監視間隔 3s では 1 ステップに見える）

Scale to Zero（3→0）:
  KEDAScaleTargetDeactivated      20:11:51    "Deactivated ... from 3 to 0"
  ↑ LastActiveTime(20:10:51) + cooldownPeriod(60s) = 20:11:51（誤差ゼロ）
```

### 2026-05-27〜28 K8s HPAScaleToZero 検証

```
観測イベント・Condition            発生タイミング
─────────────────────────────────────────────────────────────────
Scale to Zero:
  AbleToScale: ScaleDownStabilized   lag=0 検知から +5s（stabilization window カウント開始）
  SuccessfulRescale "New size: 0"    lag=0 検知から +51s（60s window 満了）
  ScaledToZero: True                 SuccessfulRescale と同時（Pod 終了前に付与）

Scale from Zero:
  SuccessfulRescale "New size: 3"    lag 検知から +15〜23s（1 ステップ）
  ScaledToZero: False                Scale from Zero と同時にクリア

メトリクス障害（D-10/D-11）:
  FailedGetExternalMetric            adapter 停止から +20s または Prometheus 停止から +51s
  ↑ Warning Event と ScalingActive=False Condition を同時に記録
```

### 実測で観測した Condition の遷移シーケンス（Scale to Zero）

```
t=0s   lag=0 検知
       ScaledToZero: False / NotScaledToZero
       AbleToScale:  True  / ReadyForNewScale

t=5s   ★ AbleToScale: True / ScaleDownStabilized  ← 初回 desiredReplicas=0 推奨

t=51s  ★ SuccessfulRescale "New size: 0"
       ★ ScaledToZero: True / ScaledToZero         ← Pod 終了前に付与
         AbleToScale:  True / SucceededRescale

t=62s  ★ AbleToScale: True / ReadyForNewScale      ← currentReplicas=0 で安定
         ScaledToZero: True / ScaledToZero          ← 維持（Scale from Zero まで）
```
