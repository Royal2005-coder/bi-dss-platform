#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

NS=bi-dss
DEST="${1:-exports-k8s}"
[ ! -e "$DEST" ] || { echo "destination already exists: $DEST"; exit 1; }

kubectl -n "$NS" apply -f deploy/bootstrap/loader-pod.yaml
kubectl -n "$NS" wait --for=condition=Ready pod/bi-dss-loader --timeout=180s
mkdir -p "$DEST"
kubectl -n "$NS" cp bi-dss-loader:/data/exports/powerbi "$DEST/powerbi"
kubectl -n "$NS" cp bi-dss-loader:/data/artifacts/dbt "$DEST/dbt"
kubectl -n "$NS" delete pod/bi-dss-loader --ignore-not-found
echo "downloaded to $DEST"
