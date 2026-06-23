#!/usr/bin/env bash
# Pushgateway に queue_length を push して HPA Scale to Zero を駆動する。
#
# 使い方:
#   ./push.sh 50    # queue_length=50 → HPA が 0→3 にスケールアップ
#   ./push.sh 0     # queue_length=0  → 約 75s 後に 3→0 にスケールダウン
#
# 仕組み: クラスタ内で一発限りの curl Pod を起動して Pushgateway に push する。
#        (Pushgateway 公式イメージには curl が無いため、別 Pod 経由で送る)
#
# 前提:
#   - kubectl が検証用クラスタに接続できる
#   - helmfile -f infra/helmfile.yaml apply 済み
#   - infra/manifests/demo/ 適用済み

set -euo pipefail

VALUE="${1:?Usage: $0 <queue_length>}"
JOB="${JOB:-demo-queue}"
POD="push-$(date +%s%N | tail -c 8)"
PG_URL="http://prometheus-pushgateway.monitoring.svc:9091/metrics/job/${JOB}"

# 一発push用の使い捨てPodを起動 (curlimages/curl)
kubectl run "${POD}" --image=curlimages/curl:8.10.1 --restart=Never --quiet \
  --command -- sh -c "printf 'queue_length %s\n' '${VALUE}' | curl -sS --data-binary @- '${PG_URL}'"

# 完了待ち → 削除
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${POD}" --timeout=30s >/dev/null
kubectl delete pod "${POD}" --wait=false >/dev/null

echo "✓ pushed: queue_length=${VALUE} (job=${JOB})"
