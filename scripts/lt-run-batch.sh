#!/bin/bash
# LT 用 Pushgateway 構成での n 回連続計測ドライバ
# VM 上で実行する。ssh 切断・スリープ・WiFi 切断に影響されない (nohup 推奨)。
#
# Usage:
#   nohup ./lt-run-batch.sh <count> [duration=180] [gap=30] [push_zero_at=90] > /tmp/lt-batch.log 2>&1 &
#
# 出力:
#   /tmp/lt-measure-batch/run-<i>/  (i=1..count) 各 run の CSV/events/log
#   /tmp/lt-measure-batch/DONE      全 run 完了後にタッチ
#   /tmp/lt-measure-batch/PROGRESS  各 run 完了ごとに番号を追記
#
# Run 間で:
#   - replicas=0 を確認 (前 run の最後で 0 になっているはず)
#   - 念のため push 0 (idempotent)

set -uo pipefail

COUNT="${1:-20}"
DURATION="${2:-180}"
GAP="${3:-30}"
PUSH_ZERO_AT="${4:-90}"
INTERVAL=5

BATCH_DIR="/tmp/lt-measure-batch"
mkdir -p "$BATCH_DIR"
rm -f "$BATCH_DIR/DONE" "$BATCH_DIR/PROGRESS"

INTERVAL_PER_RUN=$((DURATION + GAP))
echo "[$(date -u +%FT%TZ)] LT batch start: count=$COUNT, duration=${DURATION}s, gap=${GAP}s, push_zero_at=${PUSH_ZERO_AT}s"
echo "[$(date -u +%FT%TZ)] Estimated total: $((COUNT * INTERVAL_PER_RUN / 60)) min"

# ---- 補助関数: push 0 (使い捨て curl Pod) ----
push_zero() {
  local pod="lt-rb-$(date +%s%N | tail -c 8)"
  kubectl run "$pod" --image=curlimages/curl:8.10.1 --restart=Never --quiet \
    --command -- sh -c "printf 'queue_length 0\n' | curl -sS --data-binary @- 'http://prometheus-pushgateway.monitoring.svc:9091/metrics/job/demo-queue'" \
    >/dev/null 2>&1
  kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${pod}" --timeout=15s >/dev/null 2>&1 || true
  kubectl delete pod "$pod" --wait=false --ignore-not-found >/dev/null 2>&1
}

# ---- Preflight: 初期状態を 0 に揃える ----
echo "[$(date -u +%FT%TZ)] preflight: push queue_length=0 and wait for replicas=0"
push_zero
# replicas が 0 でなければ最大 100s 待つ (スケールダウン stabilization 60s + 余裕)
for i in $(seq 1 20); do
  TOTAL=$(kubectl get deploy demo-app -n default -o jsonpath='{.status.replicas}' 2>/dev/null || echo "?")
  if [[ "$TOTAL" == "0" || -z "$TOTAL" ]]; then
    echo "[$(date -u +%FT%TZ)] preflight: replicas=0 confirmed"
    break
  fi
  echo "[$(date -u +%FT%TZ)] preflight: waiting replicas=0 (current=$TOTAL)"
  sleep 5
done

# ---- メインループ ----
for i in $(seq 1 "$COUNT"); do
  OUT_DIR="$BATCH_DIR/run-$i"
  rm -rf "$OUT_DIR"
  mkdir -p "$OUT_DIR"

  echo "[$(date -u +%FT%TZ)] === run $i/$COUNT ==="

  # 各 run 前: replicas=0 確認 + 念のため push 0
  push_zero
  for j in $(seq 1 12); do
    TOTAL=$(kubectl get deploy demo-app -n default -o jsonpath='{.status.replicas}' 2>/dev/null || echo "?")
    if [[ "$TOTAL" == "0" || -z "$TOTAL" ]]; then
      break
    fi
    sleep 5
  done

  "$(dirname "$0")/lt-measure.sh" \
    --duration="$DURATION" \
    --interval="$INTERVAL" \
    --push-zero-at="$PUSH_ZERO_AT" \
    --output-dir="$OUT_DIR" \
    >> "$BATCH_DIR/measure.log" 2>&1

  echo "$i $(date -u +%FT%TZ)" >> "$BATCH_DIR/PROGRESS"
  echo "[$(date -u +%FT%TZ)] === run $i/$COUNT done ==="

  # 次の run までのギャップ (最終 run の後ろは不要)
  if [[ $i -lt $COUNT ]]; then
    sleep "$GAP"
  fi
done

touch "$BATCH_DIR/DONE"
echo "[$(date -u +%FT%TZ)] batch complete"
