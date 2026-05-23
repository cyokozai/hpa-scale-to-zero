# Component 1: メトリクス取得レイヤー — KEDA v2.16 側

## 概要

KEDA の Kafka Scaler は **sarama ライブラリで Kafka Broker に直接接続** し、
Consumer Group の lag を計算する。さらに同じ関数内でアクティブ判定も行うため、
「取得」と「判断」が一体化している。

```
KEDA operator
  └─ kafkaScaler.GetMetricsAndActivity()   ← 取得 + アクティブ判定が一体
       └─ getTotalLag()
            ├─ getConsumerOffsets()    ──┐  goroutine で並行取得
            └─ getProducerOffsets()   ──┘
                 └─ getLagForPartition() × パーティション数
```

---

## ファイル

| ファイル | 役割 |
|---|---|
| `pkg/scalers/kafka_scaler.go` | Kafka Scaler 全体（メトリクス取得・アクティブ判定・スケール仕様） |

---

## kafkaScaler 構造体
### `pkg/scalers/kafka_scaler.go:42`

```go
type kafkaScaler struct {
    metricType      v2.MetricTargetType
    metadata        kafkaMetadata    // bootstrap servers, consumer group, topic, threshold など
    client          sarama.Client    // Kafka Broker への TCP 接続を保持
    admin           sarama.ClusterAdmin // Offset の取得に使う管理クライアント
    logger          logr.Logger
    previousOffsets map[string]map[int32]int64 // excludePersistentLag 機能用の前回 offset 記録
}
```

**ポイント:** `sarama.Client` を構造体のフィールドとして保持している。
K8s 側が HTTP クライアントしか持たないのと対照的に、KEDA は TCP レベルで Broker と常時接続する。

---

## GetMetricsAndActivity — 取得とアクティブ判定の一体化
### `pkg/scalers/kafka_scaler.go:930`

```go
// メトリクス取得とアクティブ（Scale from Zero の起動判断）を同時に返す。
// K8s の GetExternalMetric が値のみ返すのと異なり、bool も返す。
func (s *kafkaScaler) GetMetricsAndActivity(_ context.Context, metricName string) ([]external_metrics.ExternalMetricValue, bool, error) {
    totalLag, totalLagWithPersistent, err := s.getTotalLag()
    if err != nil {
        return []external_metrics.ExternalMetricValue{}, false, err
    }
    metric := GenerateMetricInMili(metricName, float64(totalLag))

    // isActive の判断をここで完結させる
    // activationLagThreshold（デフォルト 0）を超えたら true → Scale from Zero が発動
    return []external_metrics.ExternalMetricValue{metric}, totalLagWithPersistent > s.metadata.activationLagThreshold, nil
}
```

**ポイント:**
- 戻り値の第2引数 `bool` が `isActive`。K8s 側の `GetExternalMetric` にはない戻り値。
- `totalLag`（スケール計算用）と `totalLagWithPersistent`（アクティブ判定用）を使い分ける。
  `excludePersistentLag=true` の場合、前回と同じ offset のパーティションは `totalLag` に加算されないが
  `totalLagWithPersistent` には加算される。アクティブ判定は永続 lag も含めて判断する設計。

---

## getTotalLag — 全パーティションの lag を集計
### `pkg/scalers/kafka_scaler.go:943`

```go
// totalLag: スケール計算に使う lag（persistentLag 除外オプションあり）
// totalLagWithPersistent: アクティブ判定に使う lag（常に全パーティション込み）
func (s *kafkaScaler) getTotalLag() (int64, int64, error) {
    topicPartitions, err := s.getTopicPartitions()
    if err != nil {
        return 0, 0, err
    }

    // consumer offset と producer offset を goroutine で並行取得
    consumerOffsets, producerOffsets, err := s.getConsumerAndProducerOffsets(topicPartitions)
    if err != nil {
        return 0, 0, err
    }

    totalLag := int64(0)
    totalLagWithPersistent := int64(0)
    totalTopicPartitions := int64(0)
    partitionsWithLag := int64(0)

    // パーティションごとに lag を計算して合計
    for topic, partitionsOffsets := range producerOffsets {
        for partition := range partitionsOffsets {
            lag, lagWithPersistent, err := s.getLagForPartition(topic, partition, consumerOffsets, producerOffsets)
            if err != nil {
                return 0, 0, err
            }
            totalLag += lag
            totalLagWithPersistent += lagWithPersistent
            if lag > 0 {
                partitionsWithLag++
            }
        }
        totalTopicPartitions += (int64)(len(partitionsOffsets))
    }

    // allowIdleConsumers=false（デフォルト）の場合、パーティション数を上限としてキャップする
    // 理由: パーティション数を超えてスケールアウトしても消費者が増えないため無意味
    if !s.metadata.allowIdleConsumers || s.metadata.limitToPartitionsWithLag {
        upperBound := totalTopicPartitions
        if s.metadata.limitToPartitionsWithLag {
            upperBound = partitionsWithLag
        }
        if (totalLag / s.metadata.lagThreshold) > upperBound {
            totalLag = upperBound * s.metadata.lagThreshold
        }
    }
    return totalLag, totalLagWithPersistent, nil
}
```

---

## getLagForPartition — パーティション単位の lag 計算
### `pkg/scalers/kafka_scaler.go:794`

```go
// lag = latestOffset（producer が書いた最新位置）- consumerOffset（consumer が読んだ位置）
func (s *kafkaScaler) getLagForPartition(
    topic string, partitionID int32,
    offsets *sarama.OffsetFetchResponse,         // consumer offset（Group Coordinator から取得）
    topicPartitionOffsets map[string]map[int32]int64, // producer offset（Broker から取得）
) (int64, int64, error) {

    block := offsets.GetBlock(topic, partitionID)
    consumerOffset := block.Offset

    // offset が未コミットの場合の処理
    if consumerOffset == invalidOffset && s.metadata.offsetResetPolicy == latest {
        retVal := int64(1) // デフォルト: lag=1 とみなしてスケール対象にする
        if s.metadata.scaleToZeroOnInvalidOffset {
            retVal = 0 // scaleToZeroOnInvalidOffset=true の場合はゼロスケール
        }
        return retVal, retVal, nil
    }

    latestOffset := topicPartitionOffsets[topic][partitionID]

    // excludePersistentLag 機能: 前回と同じ offset のパーティションは lag を 0 とみなす
    // 消費できない（stuck した）パーティションへの過剰スケールアウトを防ぐ
    if s.metadata.excludePersistentLag {
        switch previousOffset, found := s.previousOffsets[topic][partitionID]; {
        case !found:
            // 初回: 前回 offset を記録して今回は通常通り計算
            s.previousOffsets[topic][partitionID] = consumerOffset
        case previousOffset == consumerOffset:
            // 前回と同じ = 消費が進んでいない（persistent lag）
            // totalLag への加算は 0 だが lagWithPersistent には latestOffset-consumerOffset を返す
            return 0, latestOffset - consumerOffset, nil
        default:
            s.previousOffsets[topic][partitionID] = consumerOffset
        }
    }

    // 通常のlag = producer の最新 offset - consumer の現在 offset
    return latestOffset - consumerOffset, latestOffset - consumerOffset, nil
}
```

**ポイント:**
- `lag=0` の場合（consumer が追いついている）は `isActive=false` につながり Scale to Zero が発動する。
- `excludePersistentLag` は stuck パーティション対策の実装。返り値が2つ（lag, lagWithPersistent）ある理由。

---

## Consumer/Producer Offset の並行取得
### `pkg/scalers/kafka_scaler.go:903`

```go
// consumer offset と producer offset を goroutine で並行取得してレイテンシを削減
func (s *kafkaScaler) getConsumerAndProducerOffsets(topicPartitions map[string][]int32) (*sarama.OffsetFetchResponse, map[string]map[int32]int64, error) {
    consumerChan := make(chan consumerOffsetResult, 1)
    go func() {
        // Group Coordinator に OffsetFetch リクエスト（consumer が読んだ位置）
        consumerOffsets, err := s.getConsumerOffsets(topicPartitions)
        consumerChan <- consumerOffsetResult{consumerOffsets, err}
    }()

    producerChan := make(chan producerOffsetResult, 1)
    go func() {
        // Broker に ListOffsets リクエスト（producer が書いた最新位置）
        producerOffsets, err := s.getProducerOffsets(topicPartitions)
        producerChan <- producerOffsetResult{producerOffsets, err}
    }()

    consumerRes := <-consumerChan
    producerRes := <-producerChan
    // ...
    return consumerRes.consumerOffsets, producerRes.producerOffsets, nil
}
```

**ポイント:** Consumer offset（Group Coordinator）と Producer offset（Broker）を同時に取得する。
K8s 側では HTTP 1回で済む（KEDA の metrics-apiserver がこの処理を代行する）のと対照的。

---

## データフロー図

```
Kafka Broker（sarama で TCP 直接接続）
  ├─ Group Coordinator → OffsetFetch  (consumer が読んだ位置)
  └─ Broker           → ListOffsets   (producer が書いた最新位置)
           ↓ 並行取得
getLagForPartition() × パーティション数
           ↓
getTotalLag()
  totalLag              → メトリクス値（HPA の target と比較）
  totalLagWithPersistent → isActive 判定
           ↓
GetMetricsAndActivity()
  → ([]ExternalMetricValue, isActive bool, error)
           ↓
scale_handler.go の getScaledObjectState()（Component 2 で解析）
```

---

## KEDA 側の特徴まとめ

| 観点 | 内容 |
|---|---|
| Kafka との接続 | **直接**（sarama で TCP）。Group Coordinator と Broker に別々にリクエスト |
| アクティブ判定 | `GetMetricsAndActivity()` **内部で完結**。`bool` を返す |
| 2種類の lag | `totalLag`（スケール用）と `totalLagWithPersistent`（アクティブ判定用）を区別 |
| persistent lag 対策 | `excludePersistentLag` オプションで stuck パーティションを除外可能 |
| レイヤー分離 | 取得と判定が**一体**（K8s とは逆の設計思想） |
