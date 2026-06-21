# LT デモ実行手順書

K8s v1.36 alpha `HPAScaleToZero` の LT 発表用デモ手順。
当日壇上でこのファイルを開いておけば、コピペで操作が完結する想定。

- 対象 VM: `cyokozai@10.2.128.162` (hpa-test)
- クラスタ: k3d (`hpa-scale-to-zero`)、`HPAScaleToZero=true` 有効
- 想定所要時間: **約 2 分**

---

## 0. 事前準備 (LT 開始前にやる)

### 0.1 接続確認

```bash
# 別ターミナル or tmux で先につないでおく
ssh -i ~/.ssh/nc_vm_ssh cyokozai@10.2.128.162

# クラスタが生きていることを確認
kubectl get nodes
kubectl get pods -n monitoring
kubectl get hpa demo-app-hpa
```

期待する状態:
- nodes が 3 つすべて Ready
- monitoring namespace に `prometheus-server`, `prometheus-adapter`, `prometheus-pushgateway` が Running
- HPA: `REPLICAS=0`、`ScaledToZero=True`

### 0.2 初期状態を 0 に揃える (念のため)

```bash
cd ~/hpa-scale-to-zero
./scripts/lt-push.sh 0

# 60 秒待つと replicas=0 になる (すでに 0 なら何も起きない)
kubectl get hpa demo-app-hpa
# REPLICAS=0、TARGETS=-9223372036854775808m/10 (avg) を確認
```

### 0.3 tmux で 2 ペイン構成を作る

```bash
tmux new -s demo
# Ctrl+B → " (水平分割) または % (垂直分割)
# 左ペイン: watch 用
# 右ペイン: コマンド実行用
```

### 0.4 watch ペイン側の準備 (常時表示)

```bash
# Linux なら watch コマンドが使える
watch -n 1 'kubectl get hpa demo-app-hpa; echo; kubectl get deploy demo-app; echo; kubectl get pods -l app=demo-app'

# watch が使えなければ kubectl の -w で代用
# (注: HPA の -w は status の変化のみ流すので、deploy も別タブで watch するとよい)
```

### 0.5 録画保険 (推奨)

ライブデモ失敗時のフォールバック:

```bash
# 事前に asciinema で録画しておく (ネットワーク不要で再生可能)
asciinema rec demo.cast
# ...デモ操作を実行...
# Ctrl+D で録画終了

# 当日再生
asciinema play demo.cast
```

---

## 1. デモ本番フロー (発表で見せる順)

### スライド遷移と連動するタイミング想定

| LT 経過 | スライド | ここでやる操作 |
|---|---|---|
| ~4:00 | スライド 5 (構成図) を映している | (まだ操作しない、構成説明中) |
| ~4:30 | スライド 6-7 (デモ枠) に切り替え | 下記 **デモ 1** を開始 |
| ~5:30 | スライド 8 (Scale to Zero) | 下記 **デモ 2** を開始 |
| ~6:30 | スライド 9 (考察) | デモ画面はそのままで考察を話す |

---

### デモ 1: Scale from Zero (約 50 秒)

**操作ペインで実行:**

```bash
# まず replicas=0 を視聴者に見せる (大事)
kubectl get hpa demo-app-hpa
```

**期待される出力:**
```
NAME           REFERENCE             TARGETS                          MINPODS   MAXPODS   REPLICAS   AGE
demo-app-hpa   Deployment/demo-app   -9223372036854775808m/10 (avg)   0         3         0          XXm
```

**話す:** 「今、replicas が **0** です。`ScaledToZero=True` で、HPA Controller は動いていますが Pod は 1 つも居ません」

**続けて操作:**
```bash
# Scale from Zero トリガー
./scripts/lt-push.sh 50
```

**期待される出力:**
```
pod/lt-push-XXXXXXX created
✓ pushed: queue_length=50 (job=demo-queue)
```

**話す:** 「`queue_length=50` を Pushgateway に push しました。HPA がこの値を見るまで…」

**待機 (約 15-30 秒):**

watch ペインで `REPLICAS` が `0 → 3` になるのを待つ。

n=5 計測時の実測値: **平均 8.4s で SuccessfulRescale イベント**、**平均 10.4s で Pod 起動観測**。

**期待される watch ペインの遷移:**
```
[t=0-15s] REPLICAS=0 (まだメトリクス未反映)
[t=15-30s] REPLICAS=3   ← scale up!
           TARGETS=16667m/10
           Pods: demo-app-XXXX-YYYY (Running 3個)
```

**話す:** 「15 秒ほどで `REPLICAS=3` に。`TARGETS=16667m/10` は `50 / 3 = 16.667` 件/replica で、ターゲット 10 を超えているのでスケールアウト判定です」

---

### デモ 2: Scale to Zero (約 1 分)

**操作ペインで実行:**

```bash
# Scale to Zero トリガー
./scripts/lt-push.sh 0
```

**期待される出力:**
```
pod/lt-push-XXXXXXX created
✓ pushed: queue_length=0 (job=demo-queue)
```

**話す:** 「次に `queue_length=0` を push。これで HPA は `desiredReplicas=0` を計算しますが、いきなり 0 にはしません」

**待機 (約 15 秒): TARGETS が 0 に変わる**

watch ペインでは:
```
[t=0-15s]  TARGETS=16667m/10 → 0/10 (avg)
           REPLICAS=3 のまま (stabilization 待機中)
```

**話す:** 「`TARGETS` が `0/10` になりました。でも `REPLICAS=3` のままです。これは `scaleDown.stabilizationWindowSeconds: 60s` という保護機能。**60 秒間連続で『0 でよい』が続いたら初めて 0 にする**設計です」

**待機 (約 55 秒): replicas が 0 になる**

n=5 計測時の実測値: **push 0 から平均 54s で SuccessfulRescale、平均 57s で replicas=0 観測**。

**期待される watch ペインの遷移:**
```
[t=55s] REPLICAS=3 → 0
        SuccessfulRescale: New size: 0; reason: All metrics below target
        Pods: Terminating → 全部消える
```

**話す:** 「ぴったり 60 秒後ぐらいに `REPLICAS=0` になりました。Kubernetes の本体機能で、`Pod=0` の状態が維持できています。これが HPAScaleToZero の本質です」

---

## 2. 補助コマンド (Q&A タイムで使う想定)

### 2.1 External Metrics API を直接覗く

```bash
kubectl get --raw '/apis/external.metrics.k8s.io/v1beta1/namespaces/default/queue_length?labelSelector=job%3Ddemo-queue' | jq
```

**出力例:**
```json
{
  "kind": "ExternalMetricValueList",
  "apiVersion": "external.metrics.k8s.io/v1beta1",
  "items": [{
    "metricName": "queue_length",
    "metricLabels": {"job": "demo-queue"},
    "value": "50",
    "timestamp": "2026-06-18T..."
  }]
}
```

**使う場面:** 「これが HPA Controller が実際に GET している値です」

### 2.2 Feature Gate が有効か確認

```bash
# k3s の起動引数を確認
docker exec k3d-hpa-scale-to-zero-server-0 ps -ef | grep -E 'feature-gates'
```

**使う場面:** 「`HPAScaleToZero=true` が両側で有効になっています」

### 2.3 イベント履歴を見せる

```bash
kubectl get events --sort-by=.lastTimestamp -n default | tail -10
```

**出力例:**
```
SuccessfulRescale  horizontalpodautoscaler/demo-app-hpa  New size: 3; reason: external metric ...
ScalingReplicaSet  deployment/demo-app                    Scaled up replica set demo-app-XXX from 0 to 3
...
SuccessfulRescale  horizontalpodautoscaler/demo-app-hpa  New size: 0; reason: All metrics below target
ScalingReplicaSet  deployment/demo-app                    Scaled down replica set demo-app-XXX from 3 to 0
```

**使う場面:** 「これが今のデモで発生した実際のイベントです」

### 2.4 HPA の `behavior` 設定を見せる (Pods policy の罠)

```bash
kubectl get hpa demo-app-hpa -o yaml | grep -A 20 'behavior:'
```

**使う場面:** 「`Pods=3` ポリシーが入っていないと `currentReplicas=0` で `0×100%=0` になり Scale from Zero できません」

---

## 3. トラブルシューティング

### 3.1 push 後も replicas が 0 のまま動かない

**原因候補と対処:**

```bash
# (a) Pushgateway が値を保持しているか
kubectl exec -n monitoring deploy/prometheus-pushgateway -- wget -qO- http://localhost:9091/metrics | grep queue_length

# (b) Prometheus がスクレイプできているか
kubectl run -it --rm dbg --image=curlimages/curl:8.10.1 --restart=Never -- \
  curl -s 'http://prometheus-server.monitoring.svc/api/v1/query?query=queue_length'

# (c) External Metrics API に出ているか
kubectl get --raw '/apis/external.metrics.k8s.io/v1beta1' | jq

# (d) HPA の Conditions を見る
kubectl describe hpa demo-app-hpa | tail -20
```

期待: ScalingActive=True、ValidMetricFound

### 3.2 想定より遅い・速い

n=5 計測時の **個体差**:
- Scale from Zero: 3-15 秒のばらつき (Prometheus scrape タイミング次第)
- Scale to Zero: 50-60 秒 (stabilization が支配的、決定論的)

→ 当日は「ばらつきがある」と前置きすると安全

### 3.3 デモ完全失敗時のフォールバック

```bash
# 録画再生
asciinema play ~/demo.cast

# または静的スクリーンショットを画面共有
```

---

## 4. デモ後 (LT 終了後)

クラスタは次のデモ・ブログ計測のため**そのまま残す**。

もしクリーンアップしたい場合:

```bash
# replicas=0 に戻す (cleanup ではなく、初期状態に戻すだけ)
./scripts/lt-push.sh 0
sleep 75
kubectl get hpa demo-app-hpa
# REPLICAS=0 を確認
```

クラスタ自体を削除する場合:

```bash
# 注意: HPAScaleToZero feature gate と全 manifest が消える
k3d cluster delete hpa-scale-to-zero
```

---

## 5. ワンライナーまとめ (チートシート)

LT 用に手元に置く最終版:

```bash
# 接続
ssh -i ~/.ssh/nc_vm_ssh cyokozai@10.2.128.162

# 初期確認
kubectl get hpa demo-app-hpa

# Scale from Zero
cd ~/hpa-scale-to-zero && ./scripts/lt-push.sh 50

# Scale to Zero
./scripts/lt-push.sh 0

# (補助) External Metrics API を見せる
kubectl get --raw '/apis/external.metrics.k8s.io/v1beta1/namespaces/default/queue_length?labelSelector=job%3Ddemo-queue' | jq

# (補助) イベント履歴
kubectl get events --sort-by=.lastTimestamp -n default | tail -10
```

---

## 付録: tmux チートシート (デモ用最低限)

| 操作 | キー |
|---|---|
| 新規セッション開始 | `tmux new -s demo` |
| 水平分割 (左右) | `Ctrl+B` → `%` |
| 垂直分割 (上下) | `Ctrl+B` → `"` |
| ペイン移動 | `Ctrl+B` → 矢印キー |
| ペインサイズ調整 | `Ctrl+B` → `Ctrl+矢印` |
| デタッチ (LT 後復帰用) | `Ctrl+B` → `d` |
| アタッチ復帰 | `tmux attach -t demo` |
