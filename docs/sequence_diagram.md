# Scale to Zero / from Zero 往復比較シーケンス図

## 登場コンポーネント

| コンポーネント | K8s HPAScaleToZero | KEDA |
|---|---|---|
| メトリクス取得 | KEDA metrics-apiserver（HTTP）経由 | KafkaScaler が sarama で直接 |
| アクティブ判定 | HPA Controller が Condition を評価 | Scale Executor が isActive を評価 |
| ゼロ実行者 | K8s HPA Controller | KEDA Scale Executor |
| 復帰実行者 | K8s HPA Controller | KEDA Scale Executor（その後 HPA が引き継ぎ） |
| 状態の記録 | HPA Status: `ScaledToZero=True` | ScaledObject Status: `ActiveCondition=False` |

---

## K8s HPAScaleToZero — 往復シーケンス

> **読み方:** KEDA は KafkaScaler（メトリクス取得）と metrics-apiserver（HTTP エンドポイント）の役割のみ担う。
> Scale to/from Zero の判断と実行は K8s HPA Controller が行う。

```mermaid
sequenceDiagram
    autonumber

    participant KB  as Kafka Broker
    participant KS  as KEDA KafkaScaler<br/>(sarama)
    participant KC  as KEDA checkScalers<br/>(polling loop)
    participant KMA as KEDA metrics-apiserver<br/>(External Metrics API)
    participant HPA as K8s HPA Controller<br/>(horizontal.go)
    participant DEP as Deployment

    Note over KB,DEP: ── 通常稼働中: lag > 0, currentReplicas = N ──

    rect rgb(230, 245, 255)
        Note over KC,KMA: 【バックグラウンド: KEDA polling (30s 周期)】
        KC->>KS: getScaledObjectState()
        KS->>KB: sarama: OffsetFetch + ListOffsets
        KB-->>KS: consumer/producer offsets
        KS->>KS: getTotalLag() → lag = 0<br/>isActive = (0 > threshold=0) = false
        KS-->>KC: (metrics[lag=0], isActive=false)
        KC->>KMA: scaledObjectsMetricCache.StoreRecords(lag=0)
    end

    rect rgb(255, 220, 220)
        Note over HPA: 【Scale to Zero: HPA sync (15s 周期)】
        HPA->>HPA: scaleToZeroFeatureEnabled = true<br/>hasObjectOrExtMetrics = true<br/>scaledToZeroCondition = false (まだ)
        HPA->>KMA: GET /apis/external.metrics.k8s.io/v1beta1/<br/>namespaces/default/kafka-lag
        KMA-->>HPA: ExternalMetricValueList [ value=0 ]
        Note over HPA: usage = 0<br/>usageRatio = 0 / 100 = 0.0<br/>desiredReplicas = ceil(0.0) = 0<br/>minReplicas = 0 → 通過
        HPA->>DEP: scale.Update(Replicas=0)
        DEP-->>HPA: OK
        HPA->>HPA: setCondition(ScaledToZero=True)<br/>← 次回の復帰を可能にする
        Note over DEP: Pod 停止 🔴
    end

    Note over KB,DEP: ── ゼロ状態 ── HPA Status: ScaledToZero=True ──

    rect rgb(230, 245, 255)
        Note over KC,KMA: 【バックグラウンド: KEDA polling (30s 周期)】
        KC->>KS: getScaledObjectState()
        KS->>KB: sarama: OffsetFetch + ListOffsets
        KB-->>KS: lag = 500 (新規メッセージ到着)
        KS->>KS: getTotalLag() → lag = 500<br/>isActive = (500 > 0) = true
        KS-->>KC: (metrics[lag=500], isActive=true)
        KC->>KMA: scaledObjectsMetricCache.StoreRecords(lag=500)
    end

    rect rgb(255, 240, 215)
        Note over HPA: 【Scale from Zero: HPA sync (15s 周期)】
        HPA->>HPA: getScaledToZeroConditionStatus() = True<br/>canScaleFromZero = True<br/>shouldComputeMetricsForZeroReplicas() → (true, false)
        HPA->>KMA: GET /apis/external.metrics.k8s.io/v1beta1/<br/>namespaces/default/kafka-lag
        KMA-->>HPA: ExternalMetricValueList [ value=500 ]
        Note over HPA: currentReplicas = 0 → 専用パス<br/>usageRatio = 500 / 100 = 5.0<br/>desiredReplicas = ceil(5.0) = 5<br/>（readyPodCount 不使用）
        HPA->>DEP: scale.Update(Replicas=5)
        DEP-->>HPA: OK
        HPA->>HPA: setCondition(ScaledToZero=False)
        Note over DEP: Pod 5台起動 🟢
    end

    Note over KB,DEP: ── 復帰完了 ── HPA Status: ScaledToZero=False ──
```

### K8s のポイント

| フェーズ | 詳細 |
|---|---|
| **メトリクス取得** | KEDA の polling loop がキャッシュを更新 → HPA は HTTP でキャッシュから読む |
| **Scale to Zero 判断** | `desiredReplicas = 0`（メトリクス計算の結果） |
| **ゼロ後の状態記録** | `ScaledToZero=True` Condition を HPA オブジェクト（etcd）に書き込む |
| **復帰トリガー** | 次の HPA sync で `canScaleFromZero=true` を確認してメトリクス計算に進む |
| **復帰レプリカ数** | `ceil(lag / target)` — 一気に適正台数へ（例: lag=500, target=100 → 5台） |

---

## KEDA — 往復シーケンス

> **読み方:** KEDA の polling loop が Kafka Broker に直接接続し、`isActive` フラグで
> Scale to/from Zero を制御する。K8s HPA は「ゼロ→最小台数」の後の継続スケールのみ担当。

```mermaid
sequenceDiagram
    autonumber

    participant KB  as Kafka Broker
    participant KS  as KEDA KafkaScaler<br/>(sarama)
    participant KC  as KEDA checkScalers<br/>(polling loop)
    participant SE  as KEDA Scale Executor<br/>(scale_scaledobjects.go)
    participant HPA as K8s HPA<br/>(KEDA が生成)
    participant DEP as Deployment

    Note over KB,DEP: ── 通常稼働中: lag > 0, currentReplicas = N ──

    rect rgb(255, 220, 220)
        Note over KC,SE: 【Scale to Zero: KEDA polling (30s 周期)】
        KC->>KS: getScaledObjectState()
        KS->>KB: sarama: OffsetFetch + ListOffsets
        KB-->>KS: lag = 0
        KS->>KS: getTotalLag() → lag = 0<br/>isActive = (0 > threshold=0) = false
        KS-->>KC: (metrics[lag=0], isActive=false)
        KC->>SE: RequestScale(isActive=false, currentReplicas=N)

        Note over SE: isActive=false<br/>currentReplicas > 0<br/>minReplicas = 0<br/>→ scaleToZeroOrIdle()

        loop クールダウン期間中 (default 300s)
            SE->>SE: LastActiveTime + 300s > now?<br/>→ YES: ActiveCondition="ScalerCooldown"<br/>（次の polling までスキップ）
        end

        Note over SE: クールダウン経過後
        SE->>DEP: scale.Update(Replicas=0)
        DEP-->>SE: OK
        SE->>SE: ActiveCondition=False ("ScalerNotActive")<br/>Event: KEDAScaleTargetDeactivated
        Note over DEP: Pod 停止 🔴
    end

    Note over KB,DEP: ── ゼロ状態 ── ActiveCondition=False ──

    rect rgb(255, 240, 215)
        Note over KC,SE: 【Scale from Zero: KEDA polling (30s 周期)】
        KC->>KS: getScaledObjectState()
        KS->>KB: sarama: OffsetFetch + ListOffsets
        KB-->>KS: lag = 50 (新規メッセージ到着)
        KS->>KS: getTotalLag() → lag = 50<br/>isActive = (50 > threshold=0) = true
        KS-->>KC: (metrics[lag=50], isActive=true)
        KC->>SE: RequestScale(isActive=true, currentReplicas=0)

        Note over SE: isActive=true<br/>currentReplicas = 0<br/>→ scaleFromZeroOrIdle()

        SE->>SE: replicas = max(minReplicaCount=0, 1) = 1<br/>lag の大きさは参照しない
        SE->>DEP: scale.Update(Replicas=1)
        DEP-->>SE: OK
        SE->>SE: LastActiveTime = now<br/>Event: KEDAScaleTargetActivated ("triggered by kafka")
        Note over DEP: Pod 1台起動 (最小限)
    end

    rect rgb(220, 245, 220)
        Note over HPA: 【継続スケール: HPA が引き継ぎ (15s 周期)】
        Note over HPA: currentReplicas=1, readyPodCount=1<br/>lag=50, target=10<br/>usageRatio = 50/10 = 5.0<br/>desiredReplicas = ceil(5.0 × 1) = 5
        HPA->>DEP: scale.Update(Replicas=5)
        DEP-->>HPA: OK
        Note over DEP: Pod 5台起動 🟢
    end

    Note over KB,DEP: ── 復帰完了 ── ActiveCondition=True ──
```

### KEDA のポイント

| フェーズ | 詳細 |
|---|---|
| **メトリクス取得** | sarama で Kafka Broker に直接 TCP 接続（OffsetFetch + ListOffsets を並行取得） |
| **Scale to Zero 判断** | `isActive=false`（Scaler が返す bool） |
| **クールダウン** | デフォルト **300秒**。`LastActiveTime` からカウント。K8s HPA には相当機能なし |
| **ゼロ後の状態記録** | `ActiveCondition=False` + Kubernetes Event（Condition 不要で復帰できる） |
| **復帰トリガー** | `isActive=true` になった**その同じ polling 周期**で即座に実行 |
| **復帰レプリカ数** | `max(minReplicaCount, 1)` = 最小台数から起動、その後 HPA が追加スケール |

---

## 設計差異の対比

```mermaid
flowchart LR
    subgraph K8s["K8s HPAScaleToZero"]
        direction TB
        K1["Kafka Broker"] -->|sarama| K2["KEDA KafkaScaler"]
        K2 -->|HTTP cache| K3["KEDA metrics-apiserver"]
        K3 -->|ExternalMetrics API| K4["K8s HPA Controller"]
        K4 -->|scale.Update| K5["Deployment"]
        K4 -->|ScaledToZero=True| K4
    end

    subgraph KEDA["KEDA Scale to Zero"]
        direction TB
        C1["Kafka Broker"] -->|sarama| C2["KEDA KafkaScaler"]
        C2 -->|isActive bool| C3["KEDA checkScalers"]
        C3 -->|RequestScale| C4["KEDA ScaleExecutor"]
        C4 -->|scale.Update| C5["Deployment"]
        C4 -.->|min台数復帰後| C6["K8s HPA（引き継ぎ）"]
        C6 -->|scale.Update| C5
    end
```

---

## 往復タイムライン比較

```
K8s HPAScaleToZero
─────────────────────────────────────────────────────────────────────────▶ 時間
t=0    t=15s   t=30s   t=45s   t=60s
│       │       │       │       │
│ lag=0 │       │ lag>0 │       │
│ ←KEDA polling→       │
│       │ HPA sync      │ HPA sync
│       │ desiredReplicas=0     │ canScaleFromZero=true
│       │ scale.Update(0)       │ desiredReplicas=5
│       │ ScaledToZero=True     │ scale.Update(5)
│       │                       │ ScaledToZero=False
        ↑                       ↑
     ゼロへ                  復帰完了
     (待機なし)             (+約 15s)


KEDA Scale to Zero
─────────────────────────────────────────────────────────────────────────▶ 時間
t=0   t=30s  t=60s  ... t=5min  t=5min+30s
│      │      │           │         │
│ lag=0│      │           │  lag>0  │
│ ←KEDA polling→         │
│      │ isActive=false   │ isActive=true
│      │ scaleToZeroOrIdle│
│      │ [cooldown 300s]  │ scaleFromZeroOrIdle
│      │ ...cooldown中... │ scale.Update(1)
│                 │       │ +HPA → scale.Update(5)
│          scale.Update(0)│
          ↑               ↑
       ゼロへ          復帰完了
  (lag=0から+5分)     (lag>0の次polling)
```

---

## 主要な設計差異まとめ

| 観点 | K8s HPAScaleToZero | KEDA Scale to Zero |
|---|---|---|
| **Kafka 接続** | External Metrics API 経由（HTTP）| sarama で直接 TCP 接続 |
| **アクティブ判定の場所** | HPA Controller 外部（Condition を見る）| Scaler 内部（bool を返す） |
| **Scale to Zero の速さ** | 次の HPA sync（約 15s） | 次の polling（約 30s）+ クールダウン（300s）|
| **Scale from Zero の速さ** | 次の HPA sync（約 15s） | 次の polling（約 30s） |
| **初回復帰レプリカ数** | `ceil(lag / target)`（適正台数に一気に） | `max(minReplicaCount, 1)`（最小台数から） |
| **状態の保存先** | HPA Status Conditions（etcd に永続化） | ScaledObject Status（+ 毎回再計算）|
| **復帰の必要条件** | `ScaledToZero=True` Condition が必須 | `isActive=true` だけで復帰可能 |
| **スラッシング対策** | `behavior.scaleDown.stabilizationWindowSeconds` | クールダウン期間（明示的）|
| **CPU/Memory 制限** | `hasObjectOrExternalMetrics()` で弾く | `cpuMemCount` で強制 `isActive=true` |
