#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/d2"
D2="${D2:-d2}"
mkdir -p ../figures

for f in data_flow star_flight star_delay deployment; do
  "$D2" validate "$f.d2"
  "$D2" --layout elk --pad 20 --scale 2 "$f.d2" "../figures/d2_${f}.png"
done
