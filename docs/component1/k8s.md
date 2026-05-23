# Component 1: メトリクス取得レイヤー — K8s v1.36 側

## 概要

K8s の HPA は **External Metrics API** を通じてメトリクスを取得する。
Scaler（KEDA など）が提供する HTTP エンドポイントに対して REST リクエストを送り、
返ってきた値を数値スライスとして受け取るだけで、アクティブ判定は行わない。

```
HPA controller
  └─ ReplicaCalculator.GetExternalMetricReplicas()
       └─ MetricsClient.GetExternalMetric()          ← 値の取得のみ
            └─ externalclient → External Metrics API (HTTP GET)
```

---

## ファイル構成

| ファイル | 役割 |
|---|---|
| `pkg/controller/podautoscaler/metrics/interfaces.go` | `MetricsClient` インターフェース定義 |
| `pkg/controller/podautoscaler/metrics/client.go` | `restMetricsClient` 実装（External/Custom/Resource の3種） |
| `pkg/controller/podautoscaler/replica_calculator.go` | `GetExternalMetricReplicas()` — 取得値からレプリカ数を計算 |

---

## MetricsClient インターフェース
### `pkg/controller/podautoscaler/metrics/interfaces.go:40`

```go
// メトリクス取得の抽象インターフェース。実装は restMetricsClient。
type MetricsClient interface {
    // CPU/Memory など Resource メトリクス
    GetResourceMetric(ctx context.Context, resource v1.ResourceName, ...) (PodMetricsInfo, time.Time, error)
    // Pod に紐づく Custom メトリクス
    GetRawMetric(metricName string, ...) (PodMetricsInfo, time.Time, error)
    // Object に紐づく Custom メトリクス
    GetObjectMetric(metricName string, ...) (int64, time.Time, error)
    // 外部システム（Kafka lag など）の External メトリクス ← 今回の対象
    GetExternalMetric(metricName string, namespace string, selector labels.Selector) ([]int64, time.Time, error)
}
```

**ポイント:** `GetExternalMetric` の戻り値は `[]int64`（値のスライス）。
アクティブかどうかの判断値（`bool`）は含まれない。判定は呼び出し元が担う。

---

## GetExternalMetric の実装
### `pkg/controller/podautoscaler/metrics/client.go:201`

```go
// externalMetricsClient は External Metrics API (Kubernetes 拡張 API) に HTTP GET を送るだけ。
// Kafka Broker には一切触れない。KEDA の metrics-apiserver が代わりに Broker と通信する。
func (c *externalMetricsClient) GetExternalMetric(metricName, namespace string, selector labels.Selector) ([]int64, time.Time, error) {
    // External Metrics API エンドポイントへ HTTP リクエスト
    // /apis/external.metrics.k8s.io/v1beta1/namespaces/{ns}/{metricName}?labelSelector=...
    metrics, err := c.client.NamespacedMetrics(namespace).List(metricName, selector)
    if err != nil {
        return []int64{}, time.Time{}, fmt.Errorf("unable to fetch metrics from external metrics API: %v", err)
    }

    if len(metrics.Items) == 0 {
        // メトリクスが返ってこない場合はエラー（Scale to Zero の判断はしない）
        return nil, time.Time{}, fmt.Errorf("no metrics returned from external metrics API")
    }

    // 複数値をスライスで返す（合計は呼び出し元の GetExternalMetricReplicas で行う）
    res := make([]int64, 0)
    for _, m := range metrics.Items {
        res = append(res, m.Value.MilliValue())
    }
    timestamp := metrics.Items[0].Timestamp.Time
    return res, timestamp, nil
}
```

**ポイント:**
- この関数は HTTP クライアントの薄いラッパー。ビジネスロジックはゼロ。
- `metrics.Items` が空の場合はエラーを返す（「lag=0 だからゼロスケール」とはならない）。
- アクティブ判定の `bool` を返さない点が KEDA と根本的に異なる。

---

## GetExternalMetricReplicas — レプリカ数への変換
### `pkg/controller/podautoscaler/replica_calculator.go:354`

```go
// External メトリクスの値からレプリカ数を計算する。
// Scale to Zero の判断はここではなく reconcileAutoscaler (horizontal.go) で行われる。
func (c *ReplicaCalculator) GetExternalMetricReplicas(
    currentReplicas int32,
    targetUsage int64,
    metricName string,
    tolerances Tolerances,
    namespace string,
    metricSelector *metav1.LabelSelector,
    podSelector labels.Selector,
) (replicaCount int32, usage int64, timestamp time.Time, err error) {

    metricLabelSelector, err := metav1.LabelSelectorAsSelector(metricSelector)
    if err != nil {
        return 0, 0, time.Time{}, err
    }

    // ① External Metrics API から値を取得（アクティブ判定なし）
    metrics, _, err := c.metricsClient.GetExternalMetric(metricName, namespace, metricLabelSelector)
    if err != nil {
        return 0, 0, time.Time{}, fmt.Errorf("unable to get external metric %s/%s/%+v: %s", namespace, metricName, metricSelector, err)
    }

    // ② 複数パーティション分の lag を合計（Int64 オーバーフロー対策あり）
    usage = 0
    for _, val := range metrics {
        if val > 0 && usage > math.MaxInt64-val {
            usage = math.MaxInt64
            break
        }
        usage = usage + val
    }

    // ③ usageRatio = 合計lag / targetUsage でレプリカ数を計算
    usageRatio := float64(usage) / float64(targetUsage)
    replicaCount, timestamp, err = c.getUsageRatioReplicaCount(currentReplicas, usageRatio, tolerances, namespace, podSelector)
    return replicaCount, usage, timestamp, err
}
```

**ポイント:**
- `GetExternalMetric` の戻り値（`[]int64`）を合計してから `targetUsage` で割って比率を求める。
- Kafka の場合は複数パーティションの lag を合算したものが `usage` になる。
- この関数は「desiredReplicas を求める」だけ。
  0 レプリカを返せるか否かの判断は `reconcileAutoscaler` の責務。

---

## データフロー図

```
External Metrics API
(KEDAのmetrics-apiserver)
        ↑ HTTP GET
        |
externalMetricsClient.GetExternalMetric()
        ↓ []int64 (生の lag 値スライス)
ReplicaCalculator.GetExternalMetricReplicas()
        ↓ 合計 → usageRatio 計算
        ↓ int32 (desiredReplicas)
reconcileAutoscaler() in horizontal.go
        ↓ Scale to Zero の判断（Component 2/3 で解析）
```

---

## K8s 側の特徴まとめ

| 観点 | 内容 |
|---|---|
| Kafka との接続 | **しない**。KEDA の metrics-apiserver 経由で HTTP GET するだけ |
| アクティブ判定 | この層では**行わない**。戻り値に `bool` なし |
| 複数値の扱い | `[]int64` スライスで受け取り、呼び出し元で合計する |
| エラー時の挙動 | メトリクスが空なら error を返す（ゼロスケールしない） |
| レイヤー分離 | 取得（client.go）と判断（horizontal.go）が明確に分かれている |
