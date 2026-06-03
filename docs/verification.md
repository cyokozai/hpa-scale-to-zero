# 動作検証手順

## 環境

| 項目 | 値 |
|---|---|
| クラスタ | k3d (k3s v1.36.1) — `HPAScaleToZero=true` Feature Gate 有効 |
| KEDA | v2.16.0 |
| Strimzi | 1.0.0 |
| Kafka | 4.2.0 (KRaft) |
| Consumer | kafka-console-consumer.sh (`quay.io/strimzi/kafka:1.0.0-kafka-4.2.0`) |
| Prometheus | prometheus-community/prometheus 27.3.0 |
| prometheus-adapter | prometheus-community/prometheus-adapter 4.11.0 |

### 比較シナリオの構成

```
demo-topic (partitions: 3)
  ├─ consumer group: demo-consumer-group      → kafka-consumer      (KEDA ScaledObject)
  └─ consumer group: demo-consumer-group-k8s → kafka-consumer-k8s  (raw HPA, K8s HPAScaleToZero)

メトリクス供給
  KEDA 側: KEDA が sarama で直接 Broker に問い合わせ
  K8s 側:  Strimzi Kafka Exporter → Prometheus → prometheus-adapter → External Metrics API
```

| 観点 | KEDA | K8s HPAScaleToZero |
|---|---|---|
| Scale to Zero のトリガー | `cooldownPeriod: 60` (LastActiveTime 起点タイマー) | `stabilizationWindowSeconds: 60` (推奨値の時系列) |
| Scale from Zero の方式 | 0→1（KEDA直接）→ N（HPA） 2ステップ | 0→N（HPA直接） 1ステップ |
| メトリクス供給 | KEDA が sarama で TCP 直接接続 | Prometheus + prometheus-adapter 経由 HTTP |

### 互換性注意

KEDA v2.16 が使用する sarama v1.43.3 の `MaxVersion = V3_6_0_0`（Kafka 3.6 相当）。
Kafka 4.x はプロトコルバージョンネゴシエーションで後方互換を維持しているため動作すると予想されるが、
接続エラーが発生した場合はその旨を記録する。

---

## Step 0: Strimzi の namespace 監視設定

Helm でインストールした Strimzi は、デフォルトで自身の namespace (`strimzi`) のみを監視する。
`kafka` namespace の CR を処理させるには、STRIMZI_NAMESPACE の変更と RoleBinding の追加が必要。

```bash
# Strimzi Operator が kafka namespace を監視するよう変更
kubectl patch deployment strimzi-cluster-operator -n strimzi --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/env/0","value":{"name":"STRIMZI_NAMESPACE","value":"kafka"}}]'

# kafka namespace 用の RoleBinding を strimzi namespace からコピー
for rb in strimzi-cluster-operator strimzi-cluster-operator-entity-operator-delegation strimzi-cluster-operator-watched; do
  kubectl get rolebinding $rb -n strimzi -o json | \
    python3 -c "import json,sys; d=json.load(sys.stdin); d['metadata']['namespace']='kafka'; [d['metadata'].pop(k,None) for k in ['resourceVersion','uid','creationTimestamp','annotations']]; print(json.dumps(d))" | \
    kubectl apply -f -
done

# ロールアウト完了を確認
kubectl rollout status deployment/strimzi-cluster-operator -n strimzi
```

---

## Step 1: Prometheus + prometheus-adapter のインストール

```bash
# helmfile で Prometheus と prometheus-adapter を追加インストール
helmfile -f infra/helmfile.yaml apply --selector name=prometheus
helmfile -f infra/helmfile.yaml apply --selector name=prometheus-adapter

# Prometheus が起動するまで待つ
kubectl rollout status deployment/prometheus-server -n monitoring --timeout=120s

# prometheus-adapter が起動するまで待つ
kubectl rollout status deployment/prometheus-adapter -n monitoring --timeout=120s
```

**確認: External Metrics API が応答すること**
```bash
# APIグループに external.metrics.k8s.io が現れることを確認
kubectl api-versions | grep external.metrics
# → external.metrics.k8s.io/v1beta1

# まだ Kafka Exporter が起動していないためメトリクスは空でよい
kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1" | python3 -m json.tool
```

---

## Step 2: Kafka クラスタのデプロイ（Kafka Exporter 有効）

```bash
# namespace + KafkaNodePool + Kafka CR の順に適用
# kafka.yaml に kafkaExporter セクションが追加されているため
# demo-kafka-exporter Pod が追加で起動する
kubectl apply -f infra/manifests/kafka/namespace.yaml
kubectl apply -f infra/manifests/kafka/nodepool.yaml
kubectl apply -f infra/manifests/kafka/kafka.yaml

# Kafka クラスタの Ready を待つ（3〜5 分程度）
kubectl wait kafka/demo -n kafka \
  --for=condition=Ready --timeout=300s

# Topic を作成
kubectl apply -f infra/manifests/kafka/topic.yaml

# Topic Ready 確認
kubectl wait kafkatopic/demo-topic -n kafka \
  --for=condition=Ready --timeout=60s
```

**確認:**
```bash
kubectl get pods -n kafka
# NAME                                          READY   STATUS    RESTARTS   AGE
# demo-combined-0                               1/1     Running   0          Xm
# demo-entity-operator-XXXXXXXXX-XXXXX          2/2     Running   0          Xm
```

---

## Step 3: KEDA Consumer（ScaledObject）のデプロイ

```bash
kubectl apply -f infra/manifests/consumer/deployment.yaml
kubectl apply -f infra/manifests/consumer/scaledobject.yaml
```

**確認: KEDA が HPA を生成していること**
```bash
kubectl get scaledobject kafka-consumer-scaler
kubectl get hpa keda-hpa-kafka-consumer-scaler
# minReplicas=0, cooldownPeriod=60 を確認
kubectl describe hpa keda-hpa-kafka-consumer-scaler | grep -E "Min|Max|Replicas"
```

---

## Step 4: K8s HPAScaleToZero Consumer のデプロイ

```bash
kubectl apply -f infra/manifests/consumer-k8s/deployment.yaml
kubectl apply -f infra/manifests/consumer-k8s/hpa.yaml
```

**確認: Kafka Exporter のメトリクスが prometheus-adapter 経由で取得できること**
```bash
# Prometheus が Kafka Exporter をスクレイプできているか確認
kubectl port-forward -n monitoring svc/prometheus-server 9090:80 &
# ブラウザで http://localhost:9090 を開き、以下のクエリを実行
# sum(kafka_consumergroup_lag{consumergroup="demo-consumer-group-k8s"}) by (topic)

# External Metrics API 経由で HPA が取得できるか確認
kubectl get --raw \
  "/apis/external.metrics.k8s.io/v1beta1/namespaces/default/kafka_consumergroup_lag_total?labelSelector=consumergroup%3Ddemo-consumer-group-k8s%2Ctopic%3Ddemo-topic" \
  | python3 -m json.tool
# → value が consumer group の lag 合計値

# HPA の状態確認（lag=0 のため targets が 0 になることを確認）
kubectl describe hpa kafka-consumer-k8s-hpa | grep -E "Metrics|Min|Max|Replicas"
```

---

## シナリオ 1: Scale to Zero 比較（KEDA cooldown vs K8s stabilization window）

**目的:** 同じ 60 秒の待機設定で、KEDA と K8s HPA がそれぞれどのタイミング・経路で 0 にするかを比較する。

### 1-1. 両コンシューマーにメッセージを送り、スケールアップを確認

```bash
# Producer Job を実行（1000 メッセージ → 両 consumer group が消費）
kubectl apply -f infra/manifests/producer/job.yaml
kubectl wait job/kafka-producer --for=condition=Complete --timeout=60s
```

```bash
# 別ターミナルで両 Deployment を並行監視
watch -n 2 'echo "=== KEDA ===" && kubectl get deploy kafka-consumer && \
            echo "=== K8s HPA ===" && kubectl get deploy kafka-consumer-k8s && \
            echo "=== HPA ===" && kubectl get hpa'
```

**期待値（~15s以内）:**
- `kafka-consumer`: KEDA が `scaleFromZeroOrIdle()` で 0→1 後、HPA が 1→3
- `kafka-consumer-k8s`: K8s HPA が `getUsageRatioReplicaCount()` で 0→3（1ステップ）

### 1-2. Consumer が lag を消化して lag=0 になったら観察開始

両 Consumer が全メッセージを消化すると lag=0 になる。ここから 60 秒間のカウントダウンが始まる。

```bash
# KEDA 側: LastActiveTime の記録タイミングを確認
kubectl describe scaledobject kafka-consumer-scaler | grep -A 3 "Last Active Time"

# K8s HPA 側: desiredReplicas=0 の推奨が記録され始めたことを確認
kubectl describe hpa kafka-consumer-k8s-hpa | grep -E "desired|current|Metrics"
```

### 1-3. 60 秒後の Scale to Zero を比較

```bash
# KEDA 側の Scale to Zero
kubectl get events --field-selector reason=KEDAScaleTargetDeactivated --sort-by=.lastTimestamp
# → KEDAScaleTargetDeactivated: "Deactivated ... from 3 to 0"
#   LastActiveTime + cooldownPeriod(60s) を過ぎた瞬間に scaleToZeroOrIdle() が実行

# K8s HPA 側の Scale to Zero
kubectl describe hpa kafka-consumer-k8s-hpa | grep -A 5 "Conditions:"
# → ScaledToZero: True（desiredReplicas=0 が stabilizationWindowSeconds(60s) 継続後）
kubectl get events --field-selector involvedObject.name=kafka-consumer-k8s-hpa
# → SuccessfulRescale: "New size: 0"
```

**観察ポイント（Scale to Zero の差異）:**

| 観察項目 | KEDA | K8s HPAScaleToZero |
|---|---|---|
| 実装 | `scaleToZeroOrIdle()` — `LastActiveTime + cooldown < now` | `reconcileAutoscaler()` — stabilization window |
| ゼロへの経路 | Deployment を直接操作（HPA を経由しない） | HPA が scale subresource 経由 |
| 状態記録 | ScaledObject Status の `LastActiveTime` | HPA Condition `ScaledToZero=True` |
| Component | Component 3 (KEDA) | Component 3 (K8s) |

---

## シナリオ 2: Scale from Zero 比較（2ステップ vs 1ステップ）

**目的:** ゼロから復帰するときの経路の違い（KEDA の 2 ステップ vs K8s の 1 ステップ）を観察する。

**前提:** シナリオ 1 完了後（両 Deployment が 0 レプリカ）

### 2-1. Producer を再実行

```bash
kubectl delete job kafka-producer
kubectl apply -f infra/manifests/producer/job.yaml
```

### 2-2. Scale from Zero の経路を比較

```bash
# 両 Deployment の変化を記録
kubectl get events --sort-by=.lastTimestamp | grep -E "kafka-consumer|kafka-consumer-k8s"
```

**KEDA 側の期待値（2 ステップ）:**
```
KEDAScaleTargetActivated:  0 → 1  （scaleFromZeroOrIdle: max(minReplicaCount,1)=1）
HPA SuccessfulRescale:     1 → 3  （lag/lagThreshold = ceil(100/10) = 10, cap=3）
```

**K8s HPA 側の期待値（1 ステップ）:**
```
HPA SuccessfulRescale:     0 → 3  （canScaleFromZero=true, getUsageRatioReplicaCount で直接計算）
※ ScaledToZero=True Condition が存在するため currentReplicas=0 から直接 N へ
```

```bash
# K8s HPA が ScaledToZero Condition を解除するタイミング
kubectl describe hpa kafka-consumer-k8s-hpa | grep -A 3 "ScaledToZero"
# → Scale from Zero 後: ScaledToZero: False, reason: NotScaledToZero
```

**観察ポイント（Scale from Zero の差異）:**

| 観察項目 | KEDA | K8s HPAScaleToZero |
|---|---|---|
| 実装 | `scaleFromZeroOrIdle()` → `max(minReplicaCount,1)` | `getUsageRatioReplicaCount()` — `ceil(usageRatio)` |
| ステップ数 | 2 ステップ（0→1→N） | 1 ステップ（0→N） |
| N の計算タイミング | 2 回目（HPA loop） | 1 回目（即時） |
| Component | Component 4 (KEDA) | Component 4 (K8s) |

---

## 検証タイムライン（比較）

```
経過時間  KEDA (kafka-consumer)               K8s HPA (kafka-consumer-k8s)
─────────────────────────────────────────────────────────────────────────────
t=0       Producer Job 実行 (1000 messages)  ← 同じメッセージ、別 consumer group
t+15s     KEDAScaleTargetActivated: 0→1      HPA SuccessfulRescale: 0→3 (1ステップ)
          HPA SuccessfulRescale: 1→3
t+1min    全消化 → lag=0                     全消化 → lag=0
          isActive=false, LastActiveTime記録  desiredReplicas=0 の推奨開始
t+1min    ←─── 60 秒カウントダウン開始 ────→
t+2min    KEDAScaleTargetDeactivated: 3→0    HPA SuccessfulRescale: 3→0
                                              ScaledToZero=True セット
          ↑ cooldown: LastActiveTime起点      ↑ stabilization: desiredReplicas=0継続
          ↑ Deployment 直接操作              ↑ HPA 経由 scale subresource
```

---

## トラブルシューティング

### KEDA が Kafka に接続できない場合

```bash
kubectl logs -n keda deployment/keda-operator | grep -i kafka | tail -20
```

考えられる原因:
- sarama (MaxVersion=V3_6_0_0) と Kafka 4.x のプロトコル非互換
- Kafka クラスタがまだ Ready でない
- bootstrap server のアドレス誤り

### Consumer Group の lag が 0 にならない場合

```bash
# Consumer が実際に接続できているか確認
kubectl logs deployment/kafka-consumer

# Consumer Group の状態確認
kubectl exec -n kafka demo-combined-0 -- \
  /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --group demo-consumer-group \
  --describe
```

### HPA に ScaledToZero Condition が付かない場合

```bash
# HPAScaleToZero Feature Gate の確認
kubectl get --raw /api/v1 | python3 -c \
  "import json,sys; print('ok')"  # API server が動いているか確認

# KEDA が生成した HPA の minReplicas が 0 か確認
kubectl get hpa keda-hpa-kafka-consumer-scaler -o jsonpath='{.spec.minReplicas}'
# → 0 でない場合は ScaledObject の minReplicaCount を確認
```

---

## 実測結果（2026-05-23 検証済み）

### Scale to Zero（KEDA）

```
ScaledObject 作成直後:
  kafka-consumer: 1 replica（Deployment spec の初期値）

~15s 後（初回 polling + scaler rebuild 後）:
  KEDAScaleTargetDeactivated: 1 → 0
  イベント: "Deactivated apps/v1.Deployment default/kafka-consumer from 1 to 0"

状態: lag=0（消費者グループが未コミット → offsetResetPolicy=latest で即座にゼロ認定）
```

### Scale from Zero（KEDA）

```
Producer が 100 メッセージ送信後（~15s 以内）:
  1. KEDAScaleTargetActivated: 0 → 1
     イベント: "Scaled apps/v1.Deployment default/kafka-consumer from 0 to 1,
               triggered by kafkaScaler"
  2. HPA SuccessfulRescale: 1 → 3
     理由: "external metric s0-kafka-demo-topic above target"

結果: kafka-consumer が 0 → 1 → 3 の 2 ステップでスケールアップ ✓
```

### Component 分析との対応（KEDA）

| 観測イベント | コードの実装（Component） |
|---|---|
| `KEDAScaleTargetDeactivated` | `scaleToZeroOrIdle()` — `pkg/scaling/executor/scale_scaledobjects.go` (Component 3) |
| `KEDAScaleTargetActivated: 0→1` | `scaleFromZeroOrIdle()` — same file (Component 4) |
| HPA `SuccessfulRescale: 1→3` | HPA の通常ループ、`getUsageRatioReplicaCount()` (Component 4) |
| `Active=False` on ScaledObject | `getScaledObjectState()` — `pkg/scaling/scale_handler.go` (Component 2) |

---

## 実測結果（2026-05-27 K8s HPAScaleToZero 検証）

### 環境確認

```bash
# HPAScaleToZero Feature Gate が有効であることを確認
k3d cluster get hpa-scale-to-zero -o yaml | grep feature
# → --kube-apiserver-arg=feature-gates=HPAScaleToZero=true
# → --kube-controller-manager-arg=feature-gates=HPAScaleToZero=true
```

### Scale to Zero（K8s HPAScaleToZero）

```
Deployment 初期状態: kafka-consumer-k8s = 1 replica, lag = 0

60 秒（stabilizationWindowSeconds）経過後:
  HPA SuccessfulRescale: New size: 0; reason: All metrics below target

HPA Conditions（Scale to Zero 後）:
  ScaledToZero: True, reason: ScaledToZero
    → これがないと Scale from Zero が不可能（hasObjectOrExtMetrics チェックで有効化される）
  AbleToScale: True, reason: ReadyForNewScale
  ScalingActive: True, reason: ValidMetricFound  ← ゼロでもメトリクス計算継続
```

### Scale from Zero（K8s HPAScaleToZero）

```
前提: kafka-consumer-k8s = 0 replica, ScaledToZero=True

Kafka に 60 メッセージ produce → lag=60 (External Metrics API で value="60" 確認)

HPA Conditions の変化:
  ScalingLimited: True, reason: ScaleUpLimit  ← 初期状態の問題点（後述）
  
修正後（Pods ポリシー追加）:
  HPA SuccessfulRescale: New size: 3; reason: external metric kafka_consumergroup_lag_total above target
  → 0 → 3 の 1 ステップ (KEDA の 0→1→3 と対照的)

ScaledToZero Condition の変化（Scale from Zero 後）:
  ScaledToZero: False, reason: NotScaledToZero  ← 復帰後にクリアされる
```

### 重要な実装上の注意点

**`Percent: 100` ポリシー単独では Scale from Zero できない:**

```yaml
# 問題のある設定
scaleUp:
  policies:
    - type: Percent
      value: 100
      periodSeconds: 15
# currentReplicas=0 のとき: 0 * 100% = 0 → スケールアップ不可
# 症状: ScalingLimited: True, reason: ScaleUpLimit
```

```yaml
# 正しい設定（Pods ポリシーを追加）
scaleUp:
  selectPolicy: Max
  policies:
    - type: Percent
      value: 100
      periodSeconds: 15
    - type: Pods
      value: 3   # maxReplicas と同値にすることで 0→maxReplicas の 1ステップを実現
      periodSeconds: 15
```

### Component 分析との対応（K8s HPAScaleToZero）

| 観測イベント | コードの実装（Component） |
|---|---|
| HPA `SuccessfulRescale: New size: 0` | `reconcileAutoscaler()` — `stabilizationWindowSeconds` 経過後 (Component 3) |
| `ScaledToZero: True` Condition 付与 | `setCondition(hpa, ScaledToZero, True, ...)` — `horizontal.go` (Component 3) |
| ゼロでもメトリクス計算継続 | `shouldComputeMetricsForZeroReplicas()` — `canScaleFromZero=true` (Component 2) |
| HPA `SuccessfulRescale: New size: 3` | `getUsageRatioReplicaCount()` — 0→N 直接計算 (Component 4) |
| `ScaledToZero: False` クリア | Scale from Zero 後の Condition 更新 (Component 4) |

---

## 実測結果（2026-05-27 A-1: Scale from Zero レイテンシ計測）

### 計測方法

```
t0: produce コマンド完了
t1: External Metrics API が lag > 0 を返した時刻（Prometheus scrape + HPA query 完了）
t2: HPA .status.desiredReplicas > 0 になった時刻（HPA sync 完了）
t3: 最初の Pod が Running になった時刻
```

produce メッセージ数: 60、consumer group: `demo-consumer-group-k8s`、topic: `demo-topic`（3 partitions）

### 計測結果（3回）

| 回 | t1-t0 metric pipeline | t2-t1 HPA sync | t3-t2 Pod start | **t3-t0 合計** |
|---|---|---|---|---|
| 1 | 17s | 6s | 0s | **23s** |
| 2 | 13s | 3s | 1s | **17s** |
| 3 | 5s  | 6s | 0s | **11s** |
| **平均** | **11.7s** | **5.0s** | **0.3s** | **17.0s** |

### 考察

**t1-t0（metric pipeline）の理論値と実測のギャップ:**

当初の想定（最大 75s = Prometheus scrape 15s + adapter relist 60s）に対し、実測は 5〜17s だった。

原因: prometheus-adapter は `metricsRelist` でメトリクス名の発見のみを行い、**メトリクスの値は HPA からのリクエスト毎に Prometheus に直接クエリ**する。したがって実際のパイプラインは以下のとおり:

```
produce → Kafka Exporter (即時) → Prometheus scrape (≦15s scrape_interval)
        → HPA sync (≦15s sync period) → External Metrics API query (同期)
```

理論的な最大レイテンシ = 15s (scrape) + 15s (HPA sync) = **30s**
実測最大 = **23s**（理論値内）

**t2-t1（HPA sync）が 3〜6s で安定している理由:**

HPA sync period は 15s だが、3回とも 3〜6s で完了している。
これは prometheus-adapter がメトリクス値を on-demand で返すため、HPA sync が来た瞬間に最新値を返せることを示す。
15s sync period の中でのランダムな位相により最大 15s の揺らぎがあるが、今回は運良く早いタイミングで計測できた。

**t3-t2（Pod start）が 0〜1s な理由:**

k3d 環境で image pull 不要（`ImagePullPolicy: IfNotPresent` + キャッシュ済み）のため、スケジューリングとコンテナ起動がほぼ瞬時。

### KEDA との比較（想定値）

KEDA の Scale from Zero は sarama で Broker に直接 TCP 接続するため、Prometheus scrape の 15s 待機がない。

| | K8s HPAScaleToZero | KEDA v2.16 |
|---|---|---|
| メトリクス取得 | Prometheus scrape ≦15s | Broker 直接接続 ~1s |
| スケーラー判断 | HPA sync ≦15s | KEDA polling interval ~15s |
| Scale from Zero 実行 | HPA 経由（同ループ内） | scaleFromZeroOrIdle() 直接 |
| 実測合計（推定） | **11〜23s** | **~15s**（F-17 で実測予定） |

---

## 実測結果（2026-05-28 C-7: Stabilization Window 中の HPA Condition 変化）

### 計測方法

lag=0 を検知した瞬間を t=0 として、HPA Conditions を 5 秒間隔で CSV ポーリング。
stabilization window 設定値: `scaleDown.stabilizationWindowSeconds: 60`

### CSV 記録（抜粋）

```
timestamp,elapsed_s,currentReplicas,desiredReplicas,ableToScale_reason,scaledToZero_status
04:01:58,  0,  3, 3, ReadyForNewScale,    False   ← lag=0 検知直後
04:02:03,  5,  3, 3, ScaleDownStabilized, False   ← ★ カウント開始（desiredReplicas=0 推奨が蓄積開始）
04:02:08, 10,  3, 3, ScaleDownStabilized, False
...（ScaleDownStabilized が 41s 継続）...
04:02:44, 46,  3, 3, ScaleDownStabilized, False
04:02:49, 51,  3, 0, SucceededRescale,    True    ← ★ Scale to Zero 実行 + ScaledToZero=True
04:02:55, 57,  3, 0, SucceededRescale,    True    ← Pod 終了中（currentReplicas まだ 3）
04:03:00, 62,  0, 0, ReadyForNewScale,    True    ← Pod 完全終了
```

### Condition 遷移シーケンス

```
t=0s   lag=0 検知
       AbleToScale: True / ReadyForNewScale      ← まだ推奨値ヒストリーに lag>0 の記録が残っている
       ScaledToZero: False / NotScaledToZero

t=5s   ★ AbleToScale: True / ScaleDownStabilized ← 初回 desiredReplicas=0 推奨がヒストリーに追記
         → 「直近 60s の最大推奨値 = 前回の 3 」なのでまだスケールダウン保留

t=46s  ScaleDownStabilized 継続（ヒストリーが desiredReplicas=0 で埋まっていく）

t=51s  ★ AbleToScale: True / SucceededRescale    ← 60s ウィンドウが desiredReplicas=0 のみになる
       ★ ScaledToZero: True / ScaledToZero        ← 同時に Condition 付与
         desiredReplicas=0（HPA がスケール命令を発行）
         currentReplicas=3（Pod はまだ Running → 終了処理中）

t=57s  SucceededRescale 維持（Pod 終了中）

t=62s  ★ AbleToScale: True / ReadyForNewScale    ← currentReplicas=0 になり落ち着いた状態へ
       ScaledToZero: True / ScaledToZero          ← Condition は維持される
```

### 考察

**設定 60s に対し 51s で発火した理由:**

HPA sync period は 15s 間隔。lag=0 が Prometheus に反映されてから最初の HPA sync までのランダムな位相差（0〜15s）分、stabilization window のカウントが「ポーリング開始より前」にすでに始まっていた。

- ポーリング開始 (t=0) 時点で t=5s に ScaleDownStabilized が出現 → 実際の HPA 内部カウント開始はポーリング開始の ~5〜10s 前
- よって 60s window が満了したのは ポーリング上の t=51s（内部的には t=56〜61s 相当）

**ScaledToZero=True は Pod 終了前に付与される:**

`SucceededRescale` と `ScaledToZero=True` が同時に出現（t=51s）し、currentReplicas がまだ 3 のうちに付与される。
HPA が scale subresource に `replicas=0` を書き込んだ瞬間に Condition が記録されるため、実際の Pod 終了（t=62s）より 11s 早い。

**`ScaleDownStabilized` は Scale from Zero 後もリセットされない:**

Scale to Zero 後（t=62s）の `ReadyForNewScale` は「現在の推奨値 = 0 = 現状と一致」を意味する状態で、`ScaleDownStabilized` は解除される。これは新たに lag が上昇したとき stabilization なしに即スケールアップできることを示す（`scaleUp.stabilizationWindowSeconds: 0` の設定と一致）。

---

## 実測結果（2026-05-28 C-8: Stabilization Window 中のラグ再上昇）

### 検証目的と結果サマリ

**目的:** stabilization window 進行中（lag=0 から 30s 後）にメッセージを inject して、カウンターがリセットされるかを確認する

**結果:** 以下の 2 点が判明した

---

### 発見 1: 高速コンシューマーでは lag スパイクが Prometheus に捕捉されない

| 試行 | inject メッセージ数 | Kafka direct lag 最大値 | Prometheus/HPA からの lag 観測 | stabilization リセット |
|---|---|---|---|---|
| 1回目 | 15 | 15 | 0（変化なし） | なし |
| 2回目 | 100（sed 修正） | 100 | 0（変化なし） | なし |
| 3回目 | 3000 | 3000 | 0（変化なし） | なし |

**原因:** kafka-console-consumer は `max.poll.records=500` でバッチ取得し、warm 状態（パーティション割り当て済み）では **~300 msg/s** で消費する。3000 messages は ~10s で消費され、Prometheus の 15s scrape interval より短いため lag が捕捉されない。

stabilization window のリセットには **lag が ≥15s 継続**する必要があるが、この環境では ~4500 messages 以上が必要（300 msg/s × 15s）。

---

### 発見 2: Scale to Zero 直後に lag を検知すると即 Scale from Zero が発火する

`background produce (10000 messages)` のテストで以下のシーケンスが観測された:

```
t=0s   lag=0, stabilization window カウント開始（consumers=3）
t=36s  ★ 10000 messages の produce 開始（background）
t=45s  ★ Scale to Zero 発火（produce 完了前、lag まだ HPA に見えていない）
         ScaledToZero=True, currentReplicas=0 への移行開始
t=54s  Kafka direct lag=10000（produce 完了）、HPA の External Metrics はまだ古い値
t=61s  ★ HPA が lag=10000 を検知（ScaledToZero=True → canScaleFromZero=true でゼロ時もメトリクス計算継続）
         desiredReplicas=3, ScaledToZero=False
         → Scale from Zero 開始（A-1 で実測した 0→N の 1ステップ）
```

**これが示すこと:** `ScaledToZero=True` は「永続的なゼロ」ではなく、HPA が引き続きメトリクスを監視する「待機状態」である。lag が閾値を超えた瞬間に即座に Scale from Zero が発火する（stabilization window なし、`scaleUp.stabilizationWindowSeconds: 0` 設定どおり）。

### Scale to Zero → Scale from Zero の連続シーケンス（実観測）

```
lag=0 (t=0) → ScaleDownStabilized (t=5) → ScaledToZero=True (t=45)
     ↓ lag=10000 検知 (t=54〜61)
ScaledToZero=False, desiredReplicas=3 (t=61) → Scale from Zero 開始
```

この「Scale to Zero した直後に新たなメッセージで即 Scale from Zero」は本番で起こりうる重要なシナリオで、K8s alpha の `canScaleFromZero` パスが正常に機能することを示す。

---

## 実測結果（2026-05-28 D-10: prometheus-adapter 停止中の HPA 挙動）

### テスト手順

```
Phase 1: ベースライン記録（adapter 正常稼働、0 replicas/lag=0 状態）
Phase 2: kubectl scale deployment prometheus-adapter -n monitoring --replicas=0
Phase 3: kubectl scale deployment prometheus-adapter -n monitoring --replicas=1（復旧）
```

### 観測結果タイムライン

```
t=0〜41s    ScalingActive: True/ValidMetricFound   ← baseline
            External Metrics: OK:0

t=51s       adapter 停止 → External Metrics: ERR:503 ServiceUnavailable
t=51〜61s   ScalingActive: True/ValidMetricFound のまま（HPA は直前の成功結果をキャッシュ）
t=71s  ★   ScalingActive: False/FailedGetExternalMetric（約 2 HPA sync 失敗後に遷移）

t=71〜243s  ScalingActive: False のまま
            desiredReplicas=0 維持（スケールアップなし）
            currentReplicas=0 維持（ゼロ状態を安全に保持）

t=286s      adapter 復旧 → External Metrics: OK:0
t=296s ★   ScalingActive: True/ValidMetricFound に自動復帰（10s 以内）
```

### 重要な発見

**メトリクス不明時は「現状維持（フェイルセーフ）」**

`ScalingActive=False/FailedGetExternalMetric` 状態でも `desiredReplicas=0` が維持された。
HPA はメトリクスが取得できないとき、スケールアップもスケールダウンも行わない。

この挙動は `horizontal.go` の `computeReplicasForMetrics` エラーハンドリングに対応:
```go
// metrics 取得失敗 → replicas 計算をスキップ → currentReplicas を維持
if err != nil {
    setCondition(hpa, ScalingActive, False, "FailedGetExternalMetric", ...)
    return
}
```

**adapter 停止から FailedGetExternalMetric まで 20s かかる理由:**

HPA sync period = 15s。1回失敗後に `FailedGetExternalMetric` を設定するのではなく、
次の sync cycle（15s 後）で再試行し、連続失敗でようやく Condition を変更する実装のため、
実測では 2 sync cycles（~20s）後に遷移した。

**復旧は即時（10s 以内）:**

adapter が正常に応答し始めた次の HPA sync（≤15s）で `ValidMetricFound` に戻る。
ScaledToZero=True Condition および desiredReplicas=0 も維持されたまま復帰する。

| フェーズ | ScalingActive | desiredReplicas | 安全性 |
|---|---|---|---|
| 正常 | True/ValidMetricFound | 0 | ✓ |
| adapter 停止直後（~20s） | True/ValidMetricFound（キャッシュ） | 0 | ✓ |
| adapter 停止 20s 後 | False/FailedGetExternalMetric | 0 | ✓（現状維持） |
| 復旧後 10s 以内 | True/ValidMetricFound | 0 | ✓ |

---

## 実測結果（2026-05-28 D-11: Prometheus 停止中の HPA 挙動）

### テスト手順

```
Phase 1: ベースライン記録
Phase 2: kubectl scale deployment prometheus-server -n monitoring --replicas=0
Phase 3: kubectl scale deployment prometheus-server -n monitoring --replicas=1（復旧）
```

### 観測結果タイムライン

```
t=0〜30s    ScalingActive: True/ValidMetricFound   ← baseline
            External Metrics: OK:0

t=40s       Prometheus 停止 → External Metrics: ERR:InternalError（即時）
t=40〜51s   ScalingActive: True/ValidMetricFound のまま（直前の成功結果をキャッシュ）
t=91s  ★   ScalingActive: False/FailedGetExternalMetric
            ※ adapter → Prometheus の HTTP タイムアウト待ち（~30s/回）×2回分で遅延

t=91〜743s  ScalingActive: False のまま
            desiredReplicas=0 維持（D-10 と同一の安全挙動）

t=783s      Prometheus 復旧
t=816s      External Metrics: OK:0 に回復、HPA はまだ FailedGetExternalMetric
t=831s ★   ScalingActive: True/ValidMetricFound に自動復帰（~15s）
```

### D-10 との比較

| 観察項目 | D-10（adapter 停止） | D-11（Prometheus 停止） |
|---|---|---|
| External Metrics エラー種別 | 503 ServiceUnavailable | 500 InternalError |
| FailedGetExternalMetric まで | **~20s** | **~51s** |
| ポーリング実測周期 | 10s（即時レスポンス） | ~40s（HTTP タイムアウト待ち） |
| desiredReplicas 変化 | 0 のまま（安全） | 0 のまま（安全） |
| 復旧時間 | ~10s | ~15s |

### なぜ D-11 の方が FailedGetExternalMetric まで長いか

D-10 では adapter Pod 自体が消えるため API gateway が即座に 503 を返す。
D-11 では adapter は生きているが Prometheus への HTTP 接続がタイムアウトする（~30s/リクエスト）。
HPA sync period 15s に対し 1 回の External Metrics クエリが 30s ブロックするため、
実質的な sync 間隔が伸び、`FailedGetExternalMetric` への遷移が遅れる。

```
D-10: adapter 即 503 → HPA sync cycle 1回目で失敗検知（~20s）
D-11: Prometheus タイムアウト ~30s × 複数試行 → HPA sync 実質 1回/45s → 91s
```

### 共通の安全挙動

D-10 / D-11 いずれも `FailedGetExternalMetric` 状態では:
- `desiredReplicas=0` を維持し、誤ったスケールアップは発生しない
- `ScaledToZero=True` Condition も維持される
- メトリクス供給が回復した次の HPA sync（≤15s）で自動復帰する

---

### KEDA vs K8s HPAScaleToZero 実測サマリ

| 観察項目 | KEDA v2.16 | K8s HPAScaleToZero v1.36 |
|---|---|---|
| Scale to Zero 条件 | `LastActiveTime + cooldown(60s) < now` | `desiredReplicas=0` が `stabilizationWindow(60s)` 継続 |
| Scale to Zero 経路 | Deployment 直接操作 (`scaleToZeroOrIdle`) | HPA 経由 scale subresource |
| Scale from Zero ステップ数 | **2ステップ** 0→1 (`scaleFromZeroOrIdle`) → N (HPA) | **1ステップ** 0→N (HPA 直接計算) |
| Scale from Zero の N の決定 | 2回目の HPA ループで計算 | 1回目から即時 `ceil(lag / lagThreshold)` |
| ゼロ状態の記録方式 | ScaledObject Status `LastActiveTime` | HPA Condition `ScaledToZero=True` |
| メトリクス取得方式 | sarama で Broker に直接 TCP 接続 | Prometheus → prometheus-adapter → External Metrics API |
| minReplicas=0 での scaleUp 注意 | なし（KEDA が直接 0→1 するため） | `Pods` ポリシーが必須 (`Percent` 単独不可) |
