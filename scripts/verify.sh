#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

kubectl -n bi-dss get job,pod,pvc,cronjob
kubectl -n bi-dss logs job/bi-dss-build-v1 --tail=120
