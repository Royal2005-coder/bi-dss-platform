#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

NS=bi-dss
ARCHIVE="${1:-data/archive.zip}"
[ -f "$ARCHIVE" ] || { echo "archive not found: $ARCHIVE"; exit 1; }

kubectl -n "$NS" apply -f deploy/bootstrap/loader-pod.yaml
kubectl -n "$NS" wait --for=condition=Ready pod/bi-dss-loader --timeout=180s
kubectl -n "$NS" exec bi-dss-loader -- mkdir -p /data/raw

if kubectl -n "$NS" exec bi-dss-loader -- test -s /data/raw/flights_sample_3m.csv; then
  echo "raw CSV already present"
else
  kubectl -n "$NS" exec -i bi-dss-loader -- bash -c 'cat > /data/raw/archive.zip' < "$ARCHIVE"
  kubectl -n "$NS" exec bi-dss-loader -- unzip -o /data/raw/archive.zip -d /data/raw
fi

kubectl -n "$NS" exec bi-dss-loader -- ls -lh /data/raw
LINES=$(kubectl -n "$NS" exec bi-dss-loader -- wc -l /data/raw/flights_sample_3m.csv | awk '{print $1}')
[ "$LINES" = "3000001" ] || { echo "unexpected line count: $LINES"; exit 1; }

kubectl -n "$NS" delete pod/bi-dss-loader --ignore-not-found
echo "raw data ready: 3,000,000 rows"
