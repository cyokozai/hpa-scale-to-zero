# 動作検証手順

## 環境

| 項目 | 値 |
|---|---|
| クラスタ | k3d (k3s v1.36.1) — `HPAScaleToZero=true` Feature Gate 有効 |
| KEDA | v2.16.0 |
| Strimzi | 1.0.0 |
| Kafka | 4.2.0 (KRaft) |
| Consumer | kafka-console-consumer.sh (`quay.io/strimzi/kafka:1.0.0-kafka-4.2.0`) |

### 互換性注意

KEDA v2.16 が使用する sarama v1.43.3 の `MaxVersion = V3_6_0_0`（Kafka 3.6 相当）。
Kafka 4.x はプロトコルバージョンネゴシエーションで後方互換を維持しているため動作すると予想されるが、
接続エラーが発生した場合はその旨を記録する。

---

## Step 1: Kafka クラスタのデプロイ

```bash
# namespace + KafkaNodePool + Kafka CR の順に適用
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

## Step 2: Consumer と ScaledObject のデプロイ

```bash
kubectl apply -f infra/manifests/consumer/deployment.yaml
kubectl apply -f infra/manifests/consumer/scaledobject.yaml

# Consumer が起動するまで待つ
kubectl rollout status deployment/kafka-consumer

# ScaledObject が ACTIVE=true になることを確認（起動直後は lag=0 で false）
kubectl get scaledobject kafka-consumer-scaler
```

**確認: KEDA が HPA を生成していること**
```bash
kubectl get hpa
# NAME                          REFERENCE             TARGETS   MINPODS   MAXPODS   REPLICAS
# keda-hpa-kafka-consumer       Deployment/kafka-consumer   ...   0         3         1

# HPA の minReplicas=0 を確認（HPAScaleToZero の対象）
kubectl describe hpa keda-hpa-kafka-consumer | grep -E "Min|Max|Replicas"
```

---

## シナリオ 1: Scale to Zero

**目的:** lag=0 が続いた後、cooldownPeriod(300s) 経過でレプリカ数が 0 になることを確認。

### 1-1. lag を発生させてスケールアップを確認

```bash
# Producer Job を実行（1000 メッセージ送信）
kubectl apply -f infra/manifests/producer/job.yaml

# Job の完了を待つ
kubectl wait job/kafka-producer --for=condition=Complete --timeout=60s
```

```bash
# 別ターミナルで Deployment のレプリカ変化を監視
kubectl get deploy kafka-consumer -w

# KEDA の状態監視
kubectl get scaledobject kafka-consumer-scaler -w
```

**期待値:** `pollingInterval(15s)` 以内に Consumer が 1 → 3 台にスケールアップ。

```bash
# HPA のメトリクス値を確認（lag が反映されているか）
kubectl describe hpa keda-hpa-kafka-consumer | grep -A 10 "Metrics:"
```

### 1-2. Consumer が lag を消化して lag=0 に

Producer が 1000 件送り終わると Consumer が追いつき lag=0 になる。

```bash
# lag の変化を確認（KEDA が Kafka Broker から取得した値）
kubectl describe scaledobject kafka-consumer-scaler | grep -A 5 "Active:"
```

### 1-3. cooldownPeriod 経過後に Scale to Zero

**待機: lag=0 になってから約 5 分（cooldownPeriod=300s）**

```bash
# Deployment が 0 になるのを監視（5 分程度かかる）
kubectl get deploy kafka-consumer -w

# Scale to Zero 完了後に HPA の ScaledToZero Condition を確認
kubectl describe hpa keda-hpa-kafka-consumer | grep -A 3 "ScaledToZero"
# Conditions:
#   Type            Status  Reason
#   ScaledToZero    True    ScaledToZero   ← これが K8s HPAScaleToZero の証拠

# KEDA の ActiveCondition を確認
kubectl describe scaledobject kafka-consumer-scaler | grep -A 3 "Active"
# Active:  False   ScalerNotActive  ← これが KEDA の証拠

# KEDA Event を確認
kubectl get events --field-selector reason=KEDAScaleTargetDeactivated
# LAST SEEN   TYPE     REASON                        OBJECT                    MESSAGE
# Xm          Normal   KEDAScaleTargetDeactivated    ScaledObject/...          Deactivated ... from 3 to 0
```

**観察ポイント（Component 3 との対応）:**

| 観察項目 | 意味 | Component |
|---|---|---|
| `ScaledToZero=True` on HPA | K8s HPA Controller が Scale to Zero を記録 | Component 3 (K8s) |
| `KEDAScaleTargetDeactivated` Event | KEDA ScaleExecutor の `scaleToZeroOrIdle()` が実行 | Component 3 (KEDA) |
| `ActiveCondition=False` | KEDA が isActive=false を確認 | Component 2 (KEDA) |

---

## シナリオ 2: Scale from Zero

**目的:** ゼロ状態から lag が発生した直後にレプリカが復帰することを確認。

**前提:** シナリオ 1 完了後（Consumer Deployment が 0 レプリカ）

### 2-1. Producer を再実行して lag を発生させる

```bash
# 前回の Job を削除して再作成
kubectl delete job kafka-producer
kubectl apply -f infra/manifests/producer/job.yaml
```

### 2-2. Scale from Zero を監視

```bash
# Deployment のレプリカ変化を監視（2 段階スケール: 0→1→N）
kubectl get deploy kafka-consumer -w
# NAME             READY   UP-TO-DATE   AVAILABLE
# kafka-consumer   0/0     0            0          ← ゼロ状態
# kafka-consumer   0/1     1            0          ← KEDA scaleFromZeroOrIdle (1台目)
# kafka-consumer   1/1     1            1
# kafka-consumer   1/3     3            1          ← HPA が lag から 3 台を計算
# kafka-consumer   3/3     3            3          ← 全台稼働
```

```bash
# Scale from Zero の Event（どのトリガーが発火したか）
kubectl get events --field-selector reason=KEDAScaleTargetActivated
# MESSAGE: Scaled Deployment .../kafka-consumer from 0 to 1, triggered by kafka

# HPA の ScaledToZero Condition が False に戻ることを確認
kubectl describe hpa keda-hpa-kafka-consumer | grep -A 3 "ScaledToZero"
# ScaledToZero    False   NotScaledToZero
```

**観察ポイント（Component 4 との対応）:**

| 観察項目 | 意味 | Component |
|---|---|---|
| `KEDAScaleTargetActivated` (0→1) | `scaleFromZeroOrIdle()` の実行（max(minReplicaCount,1)=1） | Component 4 (KEDA) |
| HPA が 1→N にスケール | HPA が lag から desiredReplicas=ceil(lag/10)=3 を計算 | Component 4 (K8s) |
| `ScaledToZero=False` on HPA | K8s HPA Controller が復帰完了を記録 | Component 4 (K8s) |

---

## 検証タイムライン

```
経過時間  イベント
─────────────────────────────────────────────────────────────────
t=0       Producer Job 実行 (1000 messages)
t+15s     KEDA polling: lag>0 検出 → Consumer スケールアップ開始
t+30s     Consumer 3台稼働
t+1min    Consumer が全メッセージ消化 → lag=0
          (LastActiveTime が記録される)
          KEDA polling: isActive=false → scaleToZeroOrIdle() 開始
          → クールダウン 300s のカウント開始
t+6min    cooldownPeriod 経過 → Consumer を 0 にスケール ✓ Scale to Zero 完了

t+6min    [シナリオ 2 開始] Producer Job 再実行
t+6min15s KEDA polling: lag>0 検出 → isActive=true
          scaleFromZeroOrIdle() → Consumer を 1 にスケール ✓ Scale from Zero 完了
t+6min30s HPA sync: lag/lagThreshold でレプリカ数計算 → 1→3 にスケール
t+11min   Consumer が全消化 → lag=0 → 次の Scale to Zero サイクルへ
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
kubectl get hpa keda-hpa-kafka-consumer -o jsonpath='{.spec.minReplicas}'
# → 0 でない場合は ScaledObject の minReplicaCount を確認
```
