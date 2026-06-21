# LT 用 Kubernetes インフラ構成図

`infra/k3d-config.yaml` + `infra/helmfile-lt.yaml` + `infra/manifests/lt-demo/` で構築されるインフラの全体像。
Kafka 構成から Pushgateway 構成へ完全に置き換えたあとの版。

## 全体図

```mermaid
flowchart TB
    USER["👤 発表者<br/>kubectl exec + curl"]

    subgraph K3D["k3d cluster (k3s v1.36.1+k3s1)<br/>--kube-apiserver-arg=feature-gates=HPAScaleToZero=true<br/>--kube-controller-manager-arg=feature-gates=HPAScaleToZero=true"]

        APISERVER["kube-apiserver"]
        KCM["kube-controller-manager<br/>(HPA Controller を含む)"]

        subgraph NS_MON["namespace: monitoring"]
            direction TB
            PG["Pushgateway<br/>(prometheus-community/<br/>prometheus-pushgateway 3.6.1)<br/>:9091"]
            PROM["Prometheus<br/>(prometheus-community/<br/>prometheus 29.12.0)<br/>scrape_interval: 15s<br/>honor_labels: true"]
            ADAPTER["prometheus-adapter<br/>(prometheus-community/<br/>prometheus-adapter 5.3.0)<br/>queue_length rule"]
        end

        subgraph NS_DEFAULT["namespace: default"]
            direction TB
            HPA["HorizontalPodAutoscaler<br/>demo-app-hpa<br/>minReplicas=0, maxReplicas=3<br/>External Metric: queue_length<br/>(target AverageValue=10)"]
            DEP["Deployment: demo-app<br/>nginx:1.27-alpine<br/>replicas: 0↔3"]
        end

        APISERVICE[("APIService<br/>v1beta1.external.metrics.k8s.io<br/>→ prometheus-adapter")]
    end

    USER -- "① curl push<br/>queue_length=N (job=demo-queue)" --> PG
    PROM -- "② scrape 15s<br/>honor_labels=true" --> PG
    ADAPTER -- "③ PromQL<br/>sum(queue_length{...}) by (job)" --> PROM
    ADAPTER -. "registers" .-> APISERVICE
    APISERVER -- "④ External Metric query<br/>(routing via APIService)" --> APISERVICE
    KCM -- "⑤ HPA reconcile (15s)<br/>GET .../namespaces/default/queue_length" --> APISERVER
    KCM -. "⑥ patch scale subresource<br/>(0↔3 replicas)" .-> HPA
    HPA -. "scaleTargetRef" .-> DEP
```

## ノード配置

```mermaid
flowchart LR
    subgraph SRV["k3d-hpa-scale-to-zero-server-0<br/>(control-plane)"]
        CP["kube-apiserver<br/>kube-controller-manager<br/>etcd<br/>traefik (LB)"]
    end

    subgraph A0["k3d-hpa-scale-to-zero-agent-0"]
        POD_PROM["prometheus-server<br/>prometheus-pushgateway<br/>prometheus-adapter"]
    end

    subgraph A1["k3d-hpa-scale-to-zero-agent-1"]
        POD_DEMO["demo-app<br/>(replicas に応じて 0〜3)"]
    end
```

## データの流れ (Scale from Zero / Scale to Zero)

```mermaid
sequenceDiagram
    autonumber
    actor U as 発表者
    participant PG as Pushgateway
    participant P as Prometheus<br/>(scrape 15s)
    participant A as prometheus-adapter
    participant API as kube-apiserver<br/>(External Metrics API)
    participant H as HPA Controller<br/>(reconcile 15s)
    participant D as demo-app<br/>Deployment

    Note over U,D: Scale from Zero (現状 replicas=0)
    U->>PG: curl push queue_length=50
    PG-->>P: scrape 直後の最新値=50
    A->>P: PromQL sum(queue_length{job="demo-queue"})
    H->>API: GET .../namespaces/default/queue_length<br/>?labelSelector=job=demo-queue
    API->>A: route via APIService
    A-->>H: 50
    H->>H: desiredReplicas = 50/10 = 5 → maxReplicas=3 で頭打ち
    H->>D: patch scale subresource (replicas=3)
    Note over D: ~23 秒後に Pod 3 個 Running

    Note over U,D: Scale to Zero
    U->>PG: curl push queue_length=0
    PG-->>P: scrape 直後の最新値=0
    H->>API: GET → 0
    H->>H: desiredReplicas=0 だが stabilizationWindowSeconds=60s 待機
    H->>D: 60+15 秒後に patch (replicas=0)
    Note over D: Pod が 0 個に Terminating
```

## 構成要素まとめ

| Layer | コンポーネント | バージョン | 役割 |
|---|---|---|---|
| クラスタ | k3d (k3s) | v1.36.1+k3s1 | `HPAScaleToZero` Feature Gate を kube-apiserver / kube-controller-manager 両方で有効化 |
| Metrics 入口 | Pushgateway | 3.6.1 | 発表者の `curl` で push された値を保持。Pod=0 でも値が存在する性質を提供 |
| Metrics 保管 | Prometheus | 29.12.0 | 15s 間隔で Pushgateway を scrape。`honor_labels: true` で `job` を保持 |
| Metrics API ブリッジ | prometheus-adapter | 5.3.0 | `queue_length` を External Metrics API (`v1beta1.external.metrics.k8s.io`) に公開 |
| スケール対象 | nginx Deployment | 1.27-alpine | 1コンテナのみ。実処理はしない (Scale to Zero の動作実証用) |
| スケール制御 | HPA (autoscaling/v2) | — | `minReplicas=0, maxReplicas=3`、scaleUp.policies で **Pods=3 を必須に**含める |
