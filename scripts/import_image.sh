#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

docker build -t bi-dss-toolbox:1.0.0 docker
docker save bi-dss-toolbox:1.0.0 | sudo k3s ctr --namespace k8s.io images import -
sudo k3s ctr --namespace k8s.io images ls | grep bi-dss-toolbox
