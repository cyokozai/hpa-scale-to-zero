#!/bin/bash
# HPA Scale to Zero の 1 回分計測
#
# 使い方:
#   ./scripts/measure.sh <output_dir>
#
# 出力:
#   <output_dir>/measurement.csv  時刻ごとの replicas (1 秒間隔)
#   <output_dir>/triggers.csv     push 操作の時刻
#   <output_dir>/events.json      kubectl events の最終状態
#   <output_dir>/run.log          実行ログ
#
# 計測の流れ:
#   1. push 50 → 60 秒間 replicas を 1 秒ごとに記録
#   2. push 0  → 90 秒間 replicas を 1 秒ごとに記録
#   3. kubectl events を最後に 1 回取得

set -euo pipefail

OUT="${1:?Usage: $0 <output_dir>}"
SCRIPT_DIR="$(dirname "$0")"

mkdir -p "$OUT"
LOG="$OUT/run.log"
exec > >(tee -a "$LOG") 2>&1

CSV="$OUT/measurement.csv"
TRIGGERS="$OUT/triggers.csv"
echo "timestamp,phase,replicas" > "$CSV"
echo "timestamp,action" > "$TRIGGERS"

# 1 秒ごとに HPA の currentReplicas を CSV に追記する
# 注意: HPA は replicas=0 のとき .status.currentReplicas を JSON から省く (omitempty)
#       jsonpath は空文字を返すので、空なら 0 として扱う
record_until() {
  local phase="$1"
  local end_time="$2"
  while [ "$(date +%s)" -lt "$end_time" ]; do
    local ts rep
    ts=$(date -u +%FT%TZ)
    rep=$(kubectl get hpa demo-app-hpa -o jsonpath='{.status.currentReplicas}' 2>/dev/null || true)
    [ -z "$rep" ] && rep=0
    echo "${ts},${phase},${rep}" >> "$CSV"
    sleep 1
  done
}

echo "[$(date -u +%FT%TZ)] === MEASUREMENT START ==="

# Phase 1: Scale up (push 50 → 60 秒観察)
"$SCRIPT_DIR/push.sh" 50
echo "$(date -u +%FT%TZ),push 50" >> "$TRIGGERS"
record_until "scale_up" $(($(date +%s) + 60))

# Phase 2: Scale down (push 0 → 90 秒観察)
"$SCRIPT_DIR/push.sh" 0
echo "$(date -u +%FT%TZ),push 0" >> "$TRIGGERS"
record_until "scale_down" $(($(date +%s) + 90))

# events を最後に 1 回取得
kubectl get events --field-selector involvedObject.name=demo-app-hpa -o json \
  > "$OUT/events.json"

echo "[$(date -u +%FT%TZ)] === MEASUREMENT END ==="
echo "[$(date -u +%FT%TZ)] CSV rows: $(($(wc -l < "$CSV") - 1))"
