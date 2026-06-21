#!/bin/bash
# LT 用 Pushgateway 構成の単発計測スクリプト
# Kafka 版 measure.sh の Pushgateway 移植版。HPA レーンのみ。
#
# Usage:
#   ./lt-measure.sh [--start-at=<unix-epoch>] [--duration=180] [--interval=5] \
#                   [--push-zero-at=90] [--output-dir=/tmp/measure]
#
# 出力:
#   <output-dir>/measurement.csv   時系列メトリクス (aggregate.py 互換、HPAカラム)
#   <output-dir>/events.jsonl      kubectl get events --watch をリアルタイム記録
#   <output-dir>/run.log           実行ログ
#
# トリガー:
#   t=0  に push queue_length=50  → Scale from Zero
#   t=PUSH_ZERO_AT に push queue_length=0 → Scale to Zero
#
# 前提:
#   - infra/manifests/lt-demo/ 適用済み
#   - helmfile-lt.yaml 適用済み
#   - 開始時に demo-app の replicas=0 (caller が保証)

set -uo pipefail

START_AT=""
DURATION=180
INTERVAL=5
PUSH_ZERO_AT=90
OUT_DIR="/tmp/measure"

for arg in "$@"; do
  case "$arg" in
    --start-at=*)     START_AT="${arg#*=}" ;;
    --duration=*)     DURATION="${arg#*=}" ;;
    --interval=*)     INTERVAL="${arg#*=}" ;;
    --push-zero-at=*) PUSH_ZERO_AT="${arg#*=}" ;;
    --output-dir=*)   OUT_DIR="${arg#*=}" ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR"
LOG_FILE="$OUT_DIR/run.log"
exec > >(tee -a "$LOG_FILE") 2>&1

# ---- 定数 ----
DEPLOY_NAME="demo-app"
POD_LABEL="app=demo-app"
HPA_NAME="demo-app-hpa"
PUSHGATEWAY_SVC="prometheus-pushgateway.monitoring.svc:9091"
JOB="demo-queue"

# External Metrics API path
METRIC_PATH="/apis/external.metrics.k8s.io/v1beta1/namespaces/default/queue_length?labelSelector=job%3D${JOB}"

# Prometheus 直接クエリ (kubectl proxy 経由)
PROM_QUERY="queue_length%7Bjob%3D%22${JOB}%22%7D"
PROM_PATH="/api/v1/namespaces/monitoring/services/prometheus-server:80/proxy/api/v1/query?query=${PROM_QUERY}"

echo "[$(date -u +%FT%TZ)] role=hpa-pushgateway deploy=$DEPLOY_NAME hpa=$HPA_NAME duration=${DURATION}s push_zero_at=${PUSH_ZERO_AT}s interval=${INTERVAL}s out=$OUT_DIR"

# ---- 値を Pushgateway に送る関数 (使い捨て curl Pod) ----
push_value() {
  local value="$1"
  local pod="lt-m-$(date +%s%N | tail -c 8)"
  kubectl run "$pod" --image=curlimages/curl:8.10.1 --restart=Never --quiet \
    --command -- sh -c "printf 'queue_length %s\n' '$value' | curl -sS --data-binary @- 'http://${PUSHGATEWAY_SVC}/metrics/job/${JOB}'" \
    >/dev/null 2>&1
  kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${pod}" --timeout=15s >/dev/null 2>&1 || true
  kubectl delete pod "$pod" --wait=false --ignore-not-found >/dev/null 2>&1
}

# ---- events watcher をバックグラウンド起動 ----
EVENTS_FILE="$OUT_DIR/events.jsonl"
> "$EVENTS_FILE"
(
  kubectl get events -n default --watch -o json --output-watch-events 2>/dev/null \
    | jq -c --unbuffered '{
        ts: now | strftime("%Y-%m-%dT%H:%M:%SZ"),
        type: .type,
        kind: .object.kind,
        reason: .object.reason,
        involvedObject: .object.involvedObject.name,
        message: .object.message,
        eventTime: .object.lastTimestamp
      }' >> "$EVENTS_FILE" 2>/dev/null
) &
EVENTS_PID=$!
trap 'kill $EVENTS_PID 2>/dev/null; kill $(jobs -p) 2>/dev/null' EXIT

# ---- START_AT 同期待機 ----
if [[ -n "$START_AT" ]]; then
  NOW=$(date +%s)
  WAIT=$((START_AT - NOW))
  if [[ $WAIT -gt 0 ]]; then
    echo "[$(date -u +%FT%TZ)] Waiting ${WAIT}s for sync..."
    sleep "$WAIT"
  else
    echo "[$(date -u +%FT%TZ)] WARN: START_AT already past by $((-WAIT))s, starting immediately"
  fi
fi

echo "[$(date -u +%FT%TZ)] === MEASUREMENT START ==="

# ---- Scale from Zero トリガー (push 50) ----
push_value 50
echo "[$(date -u +%FT%TZ)] pushed queue_length=50 (Scale from Zero trigger)"

# ---- CSV ヘッダー (aggregate.py 互換: HPA レーンと同じ) ----
# lag_prometheus / lag_external_api には queue_length の値を入れる (列名は流用)
CSV_FILE="$OUT_DIR/measurement.csv"
echo "timestamp,replicas,current_replicas,desired_replicas,hpa_targets,hpa_conditions,lag_prometheus,lag_external_api,pod_pending,pod_containercreating,pod_running" > "$CSV_FILE"

# ---- 計測ループ ----
RUN_START=$(date +%s)
END_TIME=$((RUN_START + DURATION))
PUSH_ZERO_AT_EPOCH=$((RUN_START + PUSH_ZERO_AT))
PUSH_ZERO_DONE=0
ITER=0

while [[ $(date +%s) -lt $END_TIME ]]; do
  TS=$(date -u +%FT%TZ)
  NOW=$(date +%s)

  # Scale to Zero トリガー (push 0、1回だけ)
  if [[ $PUSH_ZERO_DONE -eq 0 && $NOW -ge $PUSH_ZERO_AT_EPOCH ]]; then
    push_value 0
    echo "[$(date -u +%FT%TZ)] pushed queue_length=0 (Scale to Zero trigger)"
    PUSH_ZERO_DONE=1
  fi

  # HPA 情報
  HPA_JSON=$(kubectl get hpa "$HPA_NAME" -n default -o json 2>/dev/null || echo "{}")
  CURRENT=$(echo "$HPA_JSON" | jq -r '.status.currentReplicas // 0')
  DESIRED=$(echo "$HPA_JSON" | jq -r '.status.desiredReplicas // 0')
  TARGETS=$(echo "$HPA_JSON" | jq -r '.status.currentMetrics[0].external.current.value // .status.currentMetrics[0].external.current.averageValue // "n/a"')
  CONDS=$(echo "$HPA_JSON" | jq -r '[.status.conditions[]? | "\(.type)=\(.status):\(.reason)"] | join(";")')

  # Deployment 情報
  DEPLOY_JSON=$(kubectl get deploy "$DEPLOY_NAME" -n default -o json 2>/dev/null || echo "{}")
  READY=$(echo "$DEPLOY_JSON" | jq -r '.status.readyReplicas // 0')
  TOTAL=$(echo "$DEPLOY_JSON" | jq -r '.status.replicas // 0')
  REPLICAS="${READY}/${TOTAL}"

  # Pod phase 分解
  POD_JSON=$(kubectl get pods -n default -l "$POD_LABEL" -o json 2>/dev/null || echo '{"items":[]}')
  P_PENDING=$(echo "$POD_JSON" | jq -r '[.items[] | select(.status.phase=="Pending")] | length')
  P_CC=$(echo "$POD_JSON" | jq -r '[.items[] | select((.status.containerStatuses // [])[]? | .state.waiting?.reason == "ContainerCreating")] | length')
  P_RUNNING=$(echo "$POD_JSON" | jq -r '[.items[] | select(.status.phase=="Running")] | length')

  # Prometheus 直接クエリ (queue_length の値、フェア比較用)
  Q_PROM=$(kubectl get --raw "$PROM_PATH" 2>/dev/null \
    | jq -r '.data.result[0].value[1] // "n/a"')

  # External Metrics API 経由の queue_length (HPA が実際に見る値)
  Q_EXT=$(kubectl get --raw "$METRIC_PATH" 2>/dev/null | jq -r '.items[0].value // "n/a"')

  # CSV 行出力 (HPA レーンと同じ列順)
  echo "${TS},${REPLICAS},${CURRENT},${DESIRED},${TARGETS},\"${CONDS}\",${Q_PROM},${Q_EXT},${P_PENDING},${P_CC},${P_RUNNING}" >> "$CSV_FILE"

  ITER=$((ITER + 1))
  if (( ITER % 6 == 0 )); then
    echo "[$(date -u +%FT%TZ)] iter=$ITER replicas=$REPLICAS desired=$DESIRED q_prom=$Q_PROM q_ext=$Q_EXT"
  fi

  sleep "$INTERVAL"
done

echo "[$(date -u +%FT%TZ)] === MEASUREMENT END ==="

# ---- cleanup ----
kill $EVENTS_PID 2>/dev/null

EVENTS_LINES=$(wc -l < "$EVENTS_FILE" 2>/dev/null || echo 0)
CSV_LINES=$(wc -l < "$CSV_FILE")
echo "[$(date -u +%FT%TZ)] CSV rows: $((CSV_LINES - 1)) | events captured: $EVENTS_LINES"
echo "[$(date -u +%FT%TZ)] Output: $OUT_DIR"
