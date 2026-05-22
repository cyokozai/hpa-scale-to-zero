# 要件定義

## 検証タイトル

HPAScaleToZero (Kubernetes v1.36 alpha) vs KEDA v2.16 Scale to Zero  
― ソースコードレベルの実装比較 ―

---

## 目的

Kafka consumer group lag をメトリクスとした Scale to Zero / from Zero の往復動作について、  
K8s 本体の alpha 実装と KEDA の設計思想・制御フローの差異をソースコードレベルで理解する。

---

## 背景・前提知識

### v1.36 での重要な仕様

- `HPAScaleToZero` Feature Gate（v1.16 から alpha、デフォルト無効）
- v1.36 で「Object / External メトリクスに限定」して動作するよう整理された
- CPU や Memory のみのワークロードは Scale to Zero の対象外（設計として明示）
- 参照 KEP: <https://kep.k8s.io/2021>
- 参照リリースノート: <https://kubernetes.io/ja/blog/2026/04/22/kubernetes-v1-36-release/#hpa-scale-to-zero-for-custom-metrics>

### 両者の関係性

- KEDA は HPA をラップして動作する（HPA を生成・管理する）
- K8s alpha は HPA 自身がゼロを扱う
- KEDA は「メトリクス取得」と「アクティブ判定」を同一関数内で行う
- K8s alpha はメトリクス取得と判定が別レイヤーに分離されている

### 重要な設計差異（事前調査済み）

| | K8s v1.36 alpha | KEDA v2.16 |
|---|---|---|
| アクティブ判定の場所 | horizontal.go（外部） | Scaler 内部（取得と一体） |
| Kafka への接続方法 | External Metrics API 経由 | sarama で直接 Broker 接続 |
| ゼロの記録方法 | HPA の Condition に記録 | ScaledObject の Status に記録 |
| 復帰判断の条件 | ScaledToZeroCondition=True | isActive=true になった瞬間 |
| 復帰時のスケール実行 | HPA 経由（通常ループ） | Deployment 直接操作 |

---

## スコープ

### 検証シナリオ

- メトリクス: Kafka consumer group lag（External metrics）
- 外部 Kafka: Strimzi on kind
- 往復: Scale to Zero → Scale from Zero を両実装で比較
- CPU メトリクスによる Scale to Zero は対象外（v1.36 で alpha 対象外と明示）

### 環境

- kind（`HPAScaleToZero: true` Feature Gate 有効）
- KEDA v2.16（helmfile 管理）
- Strimzi（helmfile 管理）
- 実行環境: ローカル Mac または PVE 上の Ubuntu（リソース次第で選択）

### 除外スコープ

- パフォーマンス計測（レイテンシ数値の比較）
- プロダクション運用設計

---

## 成果物構成

コードリーディングの成果物はコメント付きコードを基本とし、  
コンポーネントごとに比較、まとめでシーケンス図を使用する。

```
docs/
├── requirements.md        # 本要件定義
├── component1/
│   ├── k8s.md             # K8s側：メトリクス取得レイヤー
│   └── keda.md            # KEDA側：メトリクス取得レイヤー
├── component2/
│   ├── k8s.md             # K8s側：スケール判断ロジック
│   └── keda.md            # KEDA側：スケール判断ロジック
├── component3/
│   ├── k8s.md             # K8s側：Scale to Zero 実行パス
│   └── keda.md            # KEDA側：Scale to Zero 実行パス
├── component4/
│   ├── k8s.md             # K8s側：Scale from Zero 実行パス
│   └── keda.md            # KEDA側：Scale from Zero 実行パス
└── sequence_diagram.md    # 往復比較シーケンス図（Mermaid）
```

ディレクトリ構造は検証の進捗に合わせて変更可能。

---

## 作業手順

1. 要件定義を読み込み、前提を合意する
2. `infra/` に kind-config.yaml と helmfile.yaml を作成
3. k8s-1.36 と keda-2.16 を作業ディレクトリにクローン（リポジトリには含めない）
4. Component 1 から順にコメント付きコード解析を `docs/` 配下に作成
5. 各 Component は「K8s 側（k8s.md）」→「KEDA 側（keda.md）」の順で作成
6. 全 Component 完了後に `docs/sequence_diagram.md` を作成

**Component ごとに完了したら必ず commit してから次に進むこと。**

---

## コードリーディングの手がかり

### K8s 側：読むべきファイルと関数

```
pkg/features/kube_features.go
  → HPAScaleToZero の Feature Gate 定義
    {Version: version.MustParse("1.16"), Default: false, PreRelease: featuregate.Alpha}

pkg/controller/podautoscaler/horizontal.go
  → reconcileAutoscaler()                  : メインループ全体
  → shouldComputeMetricsForZeroReplicas()  : ゼロ時にメトリクス計算するか判断
  → hasObjectOrExternalMetrics()           : Object/External メトリクス存在チェック（v1.36 の肝）
  → getScaledToZeroConditionStatus()       : ScaledToZero Condition の読み取り

pkg/controller/podautoscaler/replica_calculator.go
  → GetExternalMetricReplicas()            : External メトリクスからレプリカ数を計算

pkg/controller/podautoscaler/metrics/rest_metrics_client.go
  → GetExternalMetric()                    : External Metrics API へのリクエスト
```

#### K8s Scale to Zero / from Zero の核心ロジック

```go
// horizontal.go: reconcileAutoscaler より

// ① Feature Gate と Object/External メトリクスの存在を事前チェック
scaleToZeroFeatureEnabled := utilfeature.DefaultFeatureGate.Enabled(features.HPAScaleToZero)
hasObjectOrExtMetrics := hasObjectOrExternalMetrics(hpa) // v1.36: CPU 不可・External 必須

// ② 「ゼロから復帰できるか」の条件
// ScaledToZero Condition が True かつ Object/External メトリクスがある場合のみ
scaledToZeroCondition := scaleToZeroFeatureEnabled && getScaledToZeroConditionStatus(hpa)
canScaleFromZero := scaledToZeroCondition && hasObjectOrExtMetrics

// ③ currentReplicas == 0 の場合の分岐
needsMetricComputation, shouldDisable =
    a.shouldComputeMetricsForZeroReplicas(minReplicas, scaledToZeroCondition, canScaleFromZero)

// ④ ゼロへのスケール後に Condition を記録（これがないと復帰できない）
if currentReplicas > 0 && desiredReplicas == 0 && minReplicas == 0 && hasObjectOrExtMetrics {
    setCondition(hpa, autoscalingv2.ScaledToZero, v1.ConditionTrue, ...)
}
```

### KEDA 側：読むべきファイルと関数

```
pkg/scalers/kafka_scaler.go
  → GetMetricsAndActivity()  : メトリクス取得とアクティブ判定が一体
  → getTotalLag()            : Kafka Broker への Consumer Group Lag 計算
  → getLagForPartition()     : パーティションごとの lag 計算

pkg/scaling/scale_handler.go
  → getScaledObjectState()   : 全 Scaler のアクティブ状態を集約

controllers/keda/scaledobject_controller.go
  → Reconcile()              : ScaledObject のメインループ

pkg/scaling/executor/scale_scaledobjects.go
  → RequestScale()           : Scale to Zero / from Zero の実行判断
  → scaleToZeroOrIdle()      : ゼロへの実際のスケール（Deployment 直接操作）
  → scaleFromZeroOrIdle()    : ゼロからの復帰（Deployment 直接操作）
```

#### KEDA Scale to Zero / from Zero の核心ロジック

```go
// kafka_scaler.go: メトリクス取得とアクティブ判定が一体
func (s *kafkaScaler) GetMetricsAndActivity(...) (..., bool, error) {
    totalLag, totalLagWithPersistent, _ := s.getTotalLag()
    // isActive = lagWithPersistent > activationLagThreshold
    // K8s と異なり Scaler 内部でアクティブ判定が完結する
    return metrics, totalLagWithPersistent > s.metadata.activationLagThreshold, nil
}

// scale_scaledobjects.go: 状態機械としての Scale to Zero / from Zero
// isActive=false かつ minReplicas=0 → ゼロへ
case currentReplicas > 0 && minReplicas == 0:
    e.scaleToZeroOrIdle(...)   // Deployment を直接 0 にする（HPA を経由しない）

// isActive=true かつ currentReplicas=0 → 復帰
case currentReplicas == 0:
    e.scaleFromZeroOrIdle(...) // Deployment を直接 minReplicas に戻す
```
