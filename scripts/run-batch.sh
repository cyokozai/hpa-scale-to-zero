#!/bin/bash
# n 回の measure.sh を連続実行する
#
# 使い方:
#   ./scripts/run-batch.sh [count=20]
#
# 出力:
#   /tmp/measure-batch/run-<i>/   各 run の measure.sh 出力
#   /tmp/measure-batch/DONE       全 run 完了時にタッチ
#   /tmp/measure-batch/PROGRESS   完了した run 番号
#   /tmp/measure-batch/batch.log  バッチ全体のログ
#
# 1 run = 150 秒 (60 + 90)、Run 間 gap = 30 秒
# n=20 のとき total = (150 + 30) × 20 = 60 分

set -euo pipefail

COUNT="${1:-20}"
BATCH_DIR="/tmp/measure-batch"
SCRIPT_DIR="$(dirname "$0")"

mkdir -p "$BATCH_DIR"
rm -f "$BATCH_DIR/DONE" "$BATCH_DIR/PROGRESS"

log() {
  echo "[$(date -u +%FT%TZ)] $*" | tee -a "$BATCH_DIR/batch.log"
}

log "Batch start: count=$COUNT"
log "Estimated total: $((COUNT * 180 / 60)) min"

# Preflight: 初期 replicas=0 を確認
"$SCRIPT_DIR/push.sh" 0
log "preflight: waiting replicas=0 (up to 100s)"
for _ in $(seq 1 20); do
  rep=$(kubectl get hpa demo-app-hpa -o jsonpath='{.status.currentReplicas}' 2>/dev/null || echo 0)
  [ "$rep" = "0" ] && break
  sleep 5
done

# メインループ
for i in $(seq 1 "$COUNT"); do
  RUN_DIR="$BATCH_DIR/run-$i"
  rm -rf "$RUN_DIR"
  log "=== Run $i/$COUNT start ==="
  "$SCRIPT_DIR/measure.sh" "$RUN_DIR"
  log "=== Run $i/$COUNT done ==="
  echo "$i" >> "$BATCH_DIR/PROGRESS"
  [ "$i" -lt "$COUNT" ] && sleep 30
done

touch "$BATCH_DIR/DONE"
log "Batch complete"
