# HPAScaleToZero vs KEDA Scale to Zero

Kubernetes v1.36 alpha の `HPAScaleToZero` と KEDA v2.16 の Scale to Zero を  
**ソースコードレベル** で比較検証するリポジトリ。

メトリクスは Kafka consumer group lag（External metrics）を使用し、  
Scale to Zero → Scale from Zero の往復動作を両実装で追跡する。

---

## 背景

| 項目 | K8s v1.36 alpha | KEDA v2.16 |
|---|---|---|
| アクティブ判定の場所 | `horizontal.go`（外部） | Scaler 内部（取得と一体） |
| Kafka への接続方法 | External Metrics API 経由 | sarama で直接 Broker 接続 |
| ゼロの記録方法 | HPA の Condition に記録 | ScaledObject の Status に記録 |
| 復帰判断の条件 | ScaledToZeroCondition=True | isActive=true になった瞬間 |
| 復帰時のスケール実行 | HPA 経由（通常ループ） | Deployment 直接操作 |

---

## ドキュメント構成

```
docs/
├── requirements.md        # 要件定義
├── assumptions-*.md       # 前提確認と合意事項
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

---

## インフラ構成（2 VM 分離）

環境干渉ゼロでのフェアな比較のため、**HPA 用 VM と KEDA 用 VM を別々に用意**して
それぞれに独立した k3d クラスターを構築する。

```
infra/
├── k3d-config.yaml       # 共通: k3d クラスター設定（HPAScaleToZero Feature Gate 有効、k3s v1.36）
├── helmfile.yaml         # 共通: Strimzi 1.0.0 + Prometheus 27.3.0（scrape 15s）
├── helmfile-k8s.yaml     # HPA 用 VM 専用: prometheus-adapter (External Metrics API)
├── helmfile-keda.yaml    # KEDA 用 VM 専用: KEDA Operator
└── manifests/
    ├── kafka/            # 共通: KafkaNodePool + Kafka CR + KafkaTopic
    ├── consumer/         # KEDA 用: kafka-consumer + ScaledObject
    ├── consumer-k8s/     # HPA 用: kafka-consumer-k8s + HorizontalPodAutoscaler
    └── producer/         # 共通: Producer Job
```

> **注:** `kindest/node:v1.36` が未リリースのため k3d + `rancher/k3s:v1.36.1-k3s1` を使用する。
> 実行環境は PVE 上の Ubuntu 24.04 VM。

### セットアップ手順

両 VM 共通の事前準備として `docker`, `kubectl`, `k3d`, `helm`, `helmfile` をインストールしておく。
リポジトリを clone した状態から開始する。

#### HPA 用 VM

```bash
# 1. k3d クラスター作成（HPAScaleToZero Feature Gate 有効）
k3d cluster create --config infra/k3d-config.yaml

# 2. 共通: Strimzi + Prometheus
helmfile -f infra/helmfile.yaml sync

# 3. HPA 用: prometheus-adapter
helmfile -f infra/helmfile-k8s.yaml sync

# 4. Strimzi が kafka namespace を監視するよう設定 → Kafka CR / Topic デプロイ
#    （詳細は docs/verification.md Step 0〜2 を参照）
kubectl apply -f infra/manifests/kafka/

# 5. HPA 用 Consumer + HPA をデプロイ
kubectl apply -f infra/manifests/consumer-k8s/
```

#### KEDA 用 VM

```bash
# 1. k3d クラスター作成（HPAScaleToZero Feature Gate は KEDA 側では不要だが、揃えるため有効）
k3d cluster create --config infra/k3d-config.yaml

# 2. 共通: Strimzi + Prometheus
helmfile -f infra/helmfile.yaml sync

# 3. KEDA 用: KEDA Operator
helmfile -f infra/helmfile-keda.yaml sync

# 4. Kafka CR / Topic デプロイ
kubectl apply -f infra/manifests/kafka/

# 5. KEDA 用 Consumer + ScaledObject をデプロイ
kubectl apply -f infra/manifests/consumer/
```

両 VM で同じ `Producer Job` を流して、HPA / KEDA それぞれの Scale to Zero ↔ Scale from Zero 挙動を比較する。

```bash
# 検証実行（両 VM で同じコマンド）
kubectl apply -f infra/manifests/producer/job.yaml
```

> **注意:** prometheus-adapter と KEDA は `v1beta1.external.metrics.k8s.io` APIService を取り合うため、
> 同一クラスターには共存できない。これが「VM 分離」が必要な理由でもある。

### 作業用ソースコードのクローン（リポジトリには含めない）

```bash
git clone --depth=1 --branch release-1.36 \
  https://github.com/kubernetes/kubernetes.git k8s-1.36

git clone --depth=1 --branch v2.16.0 \
  https://github.com/kedacore/keda.git keda-2.16
```

---

## ブランチ運用

```
main
 └─ dev
     └─ feat/*** ─── 作業 ─── PR ──→ dev ─── PR ──→ main
```

コミットメッセージテンプレートの設定（初回のみ）:

```bash
git config commit.template .gitmessage
```

---

## 参照

- KEP-2021: <https://kep.k8s.io/2021>
- Kubernetes v1.36 リリースノート（HPAScaleToZero）:  
  <https://kubernetes.io/ja/blog/2026/04/22/kubernetes-v1-36-release/#hpa-scale-to-zero-for-custom-metrics>
- KEDA v2.16: <https://keda.sh>
- Strimzi: <https://strimzi.io>
