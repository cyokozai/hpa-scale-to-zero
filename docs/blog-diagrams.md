# blog-draft.md 用 図版ドラフト (Mermaid)

レビュー用に7枚を1ファイルにまとめたもの。OKならそのまま `blog-draft.md` に差し込む。

---

## ① §2.1 K8s HPA 制御フロー (新規追加)

KEDA 側だけ Mermaid 図があるので対比のために HPA 側も同粒度で追加。

```mermaid
flowchart LR
    HPA[HorizontalPodAutoscaler] --> HC[HPA Controller<br/>horizontal.go]
    HC -- HTTP poll 15s --> API[External Metrics API<br/>v1beta1.external.metrics.k8s.io]
    API --> ADAPTER[prometheus-adapter]
    ADAPTER -- PromQL --> PROM[(Prometheus<br/>scrape 15s)]
    PROM -- HTTP scrape --> EXPORTER[Kafka Exporter]
    EXPORTER -- Kafka protocol --> KAFKA[(Kafka Broker)]
    HC --> SCALE[scale subresource]
    SCALE --> DEP[Deployment 0↔N]
```

**メッセージ**: 経路が 4 段（Exporter → Prom → adapter → External Metrics API）。後段の §6 で実測される ~9 秒のレイテンシ差はここに起因する。

---

## ② §4.2 2 VM 物理分離アーキテクチャ (ASCII art を差し替え)

```mermaid
flowchart TB
    PROD["Producer Job<br/>1000 messages × n=20"]

    subgraph HPA_VM["hpa-test VM &nbsp;&nbsp;(k3d, HPAScaleToZero=true)"]
        direction LR
        K1[(Kafka Broker<br/>demo-topic 3 partitions)]
        EX[Kafka Exporter]
        P1[(Prometheus<br/>15s scrape)]
        AD[prometheus-adapter]
        EM1[External Metrics API]
        H1[HPA Controller]
        D1["kafka-consumer-k8s<br/>0↔3 replicas"]

        K1 -.lag.-> EX --> P1 --> AD --> EM1 --> H1 --> D1
        D1 -.consume.-> K1
    end

    subgraph KEDA_VM["keda-test VM &nbsp;&nbsp;(k3d)"]
        direction LR
        K2[(Kafka Broker<br/>demo-topic 3 partitions)]
        KOP[KEDA Operator]
        SO[ScaledObject]
        H2[HPA 自動生成]
        D2["kafka-consumer<br/>0↔3 replicas"]

        KOP <-->|sarama TCP<br/>直接接続| K2
        KOP --> SO --> H2 --> D2
        D2 -.consume.-> K2
    end

    PROD -.並列投入.-> K1
    PROD -.並列投入.-> K2
```

**メッセージ**: 同じ Producer 入力に対して、左 = 4 段経路 (HTTP)、右 = 直接 TCP の 2 系統が同条件で並走している。

---

## ③ §6.1 タイムライン (ASCII を差し替え、n=20 平均)

`sequenceDiagram` で 2 レーン並列に表現。

```mermaid
sequenceDiagram
    autonumber
    participant P as Producer
    participant KK as KEDA VM<br/>Kafka
    participant KO as KEDA Operator
    participant KD as kafka-consumer<br/>(KEDA target)
    participant HK as HPA VM<br/>Kafka
    participant HE as Kafka Exporter →<br/>Prom → adapter
    participant HC as HPA Controller
    participant HD as kafka-consumer-k8s<br/>(HPA target)

    P->>KK: t=0  1000 messages
    P->>HK: t=0  1000 messages (並列)

    Note over KO,KD: KEDA レーン
    KK-->>KO: t+14s lag 検知 (sarama)
    KO->>KD: t+14s scaleFromZeroOrIdle 0→1
    KO->>KD: t+18s HPA 1→3 (同一秒内)
    KD-->>KK: t+~17s consume 開始

    Note over HC,HD: HPA レーン
    HK-->>HE: scrape 15s 経路
    HE-->>HC: t+23s External Metric 反映
    HC->>HD: t+23s SuccessfulRescale 0→3 (1 ステップ)
    HD-->>HK: t+26s consume 開始

    Note over KO,HD: 全消化後 (lag=0)
    KO->>KD: t+74s Deactivated 3→0 (cooldown 60s)
    HC->>HD: t+88s Rescale 3→0 (stabilization 60s + 経路遅延)
```

**メッセージ**: KEDA は 14s/74s、HPA は 23s/88s。差分はそれぞれ +9s, +14s で §6.2 の Welch's t-test とほぼ一致。

---

## ④ §3 HPAScaleToZero alpha のライフライン

7 年塩漬けを時系列で見せる。

```mermaid
timeline
    title HPAScaleToZero Feature Gate ライフライン (2019-2026)
    2019 v1.16 : alpha 導入<br/>Default false
    2020 v1.18-v1.20 : 変更なし
    2021 v1.21-v1.23 : 変更なし
    2022 v1.24-v1.26 : 変更なし
    2023 v1.27-v1.29 : 変更なし
    2024 v1.30-v1.32 : 変更なし
    2025 v1.33-v1.35 : 変更なし
    2026 v1.36 : 依然 alpha<br/>(7 年経過)
```

**メッセージ**: 20 リリース以上 alpha 据え置き。KEP プロセスの慣性が「実質マネージド K8s 不可」状況を作っている。

---

## ⑤ §4.1 APIService 衝突 (なぜ 2 VM 分離が必要か)

```mermaid
flowchart TB
    APIServer[kube-apiserver]

    subgraph NG["❌ 同一クラスタ内で KEDA + adapter を共存させた場合"]
        APISVC["APIService<br/>v1beta1.external.metrics.k8s.io"]
        KEDA[KEDA Operator<br/>metrics server]
        ADAPTER[prometheus-adapter<br/>metrics server]

        APISVC -. 後勝ち .-> KEDA
        APISVC -. 後勝ち .-> ADAPTER
        KEDA x--x ADAPTER
    end

    APIServer --> APISVC

    NOTE[後から作った方が APIService を奪う<br/>→ 片方しか応答できずフェア比較不可<br/>→ 物理 VM 2 台に分離して解決]

    NG --- NOTE
```

**メッセージ**: 既存実装で「同一クラスター上で並走比較」が見当たらない構造的理由を 1 枚で説明。

---

## ⑥ §8 コンポーネント数の対比

```mermaid
flowchart LR
    subgraph KEDA_SIDE["KEDA: 1 コンポーネント"]
        K1[KEDA Operator v2.16]
    end

    subgraph HPA_SIDE["K8s HPA + HPAScaleToZero: 3 コンポーネント"]
        H1[Kafka Exporter]
        H2[(Prometheus)]
        H3[prometheus-adapter]
        H1 --> H2 --> H3
    end

    KEDA_SIDE -. 同等機能 .-> HPA_SIDE
```

**メッセージ**: 機能が等価でも運用面のコンポーネント数が 1:3。FinOps/Platform 視点では決め手になる。

---

## ⑦ §7 障害時の状態遷移 (D-10 / D-11)

```mermaid
stateDiagram-v2
    [*] --> Normal: External Metric 正常応答

    Normal --> LastValueHold: prometheus-adapter 停止 (D-10)<br/>または Prometheus 停止 (D-11)
    LastValueHold --> Normal: 復旧

    state LastValueHold {
        [*] --> Hold
        Hold --> Hold: FailedGetExternalMetric event<br/>currentReplicas 不変<br/>(Scale to Zero されない)
        Hold --> Hold: 同時に Scale from Zero も<br/>新規 metric を取れない
    }
```

**メッセージ**: 「最後の値保持」は保護的な設計だが、副作用として「メトリクス源が消えると 0 化も復帰もできなくなる」という非対称な挙動を可視化。

---

## 差し込み先サマリ

| 図 | 対象セクション | 操作 |
|---|---|---|
| ① | §2.1 | 既存 K8s 説明文の直後に追加 (KEDA 図と並ぶ位置) |
| ② | §4.2 | 既存 ASCII art を **差し替え** |
| ③ | §6.1 | 既存テキストタイムラインを **差し替え** |
| ④ | §3 | `kube_features.go` 引用直後に追加 |
| ⑤ | §4.1 | 「同一クラスター内で共存できません」段落の直後に追加 |
| ⑥ | §8 | 比較表の前に追加 (運用面の主張を補強) |
| ⑦ | §7 | 表の直後に追加 |


```mermaid
flowchart TB
    subgraph K3D["hpa-test VM の k3d クラスタ"]
        subgraph NS_STRIMZI["namespace: strimzi"]
            OP[Strimzi Operator]
        end

        subgraph NS_KAFKA["namespace: kafka"]
            subgraph KAFKA_POD["Kafka Pod (1台、KRaft combined)"]
                CTRL[Controller役]
                BRK[Broker役]
                subgraph TOPIC["Topic: demo-topic"]
                    P0[Partition 0]
                    P1[Partition 1]
                    P2[Partition 2]
                end
                OFF[("__consumer_offsets<br/>内部Topic")]
            end
            EO["Entity Operator<br/>(Topic / User Operator)"]
            KE["Kafka Exporter<br/>:9404 kafka_consumergroup_lag"]
        end

        subgraph NS_DEFAULT["namespace: default"]
            CG["Consumer Deployment<br/>kafka-consumer-k8s<br/>(replicas: 0↔3)<br/>group=demo-consumer-group-k8s"]
            HPA["HPA<br/>minReplicas=0, maxReplicas=3"]
            PROD["Producer Job<br/>1000 messages"]
        end

        subgraph NS_MON["namespace: monitoring"]
            PROM[(Prometheus<br/>15s scrape)]
            ADAPTER[prometheus-adapter]
        end

        EMA[External Metrics API<br/>v1beta1.external.metrics.k8s.io]
    end

    OP -.manages.-> KAFKA_POD
    OP -.manages.-> EO
    OP -.manages.-> KE

    PROD -->|append| TOPIC
    CG -->|consume + commit offset| BRK
    BRK <--> OFF

    KE -->|read lag via Kafka protocol| BRK
    PROM -->|scrape| KE
    ADAPTER --> PROM
    ADAPTER --> EMA
    HPA -->|GET lag| EMA
    HPA -->|patch scale| CG
```
