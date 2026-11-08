# -*- coding: utf-8 -*-
"""8.4.2 end-to-end pipeline: DuckDB warehouse -> pre-event features -> T-60 ensemble -> Power BI CSV.

Steps (each skipped when its output already exists, unless --force):
  1 features_base      -> work/features.parquet
  2 features_v8        -> work/features_v8.parquet      (needs data/weather_daily.parquet)
  3 nowcast  (T-60)    -> work/nowcast_h60.parquet       (aircraft-history proxy + airport nowcast)
  4 network  (T-60)    -> work/network_h60_v2.parquet    (national / carrier / region, UTC clock)
  5 train_final_v14    -> outputs/ (DSS_Flight_Prediction.csv, DSS_Priority_Tier.csv, models/, figures/, summary)
  6 audit_leakage      -> outputs/leakage_audit_v14.json (independent brute-force re-computation)
  7 variable_dictionary-> outputs/variable_dictionary_pre_event_v14.csv (all features, known_time)

Usage:
  python run_pipeline.py --db /path/warehouse.duckdb [--version v15|v14] [--work DIR] [--out DIR]
                         [--from-step N] [--to-step M] [--force]
  v15 (default): steps 1-7 = meteostat, features_all, nowcast, network, weather, train_final_v15, audit
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")


def step_list_v15(work: str, out: str) -> list[tuple[int, str, list[str], str]]:
    """v15: 2019-2023 history, hourly weather <= T_pred, XGB+LGBM+CatBoost, monthly walk-forward, DSS layer."""
    return [
        (1, "fetch_meteostat", ["fetch_meteostat.py"], os.path.join(ROOT, "data", "weather_hourly_utc.parquet")),
        (2, "features_all", ["features_all.py"], os.path.join(work, "features_all.parquet")),
        (3, "nowcast_h60", ["build_nowcast_features.py", "--horizon", "60"], os.path.join(work, "nowcast_h60.parquet")),
        (4, "network_h60", ["build_network_nowcast.py", "--horizon", "60"], os.path.join(work, "network_h60_v2.parquet")),
        (5, "weather_h60", ["build_weather_nowcast.py", "--horizon", "60"], os.path.join(work, "weather_h60.parquet")),
        (6, "train_final_v15", ["train_final_v15.py"], ""),
        (7, "audit_leakage", ["audit_leakage.py", "--report", os.path.join(out, "leakage_audit_v15.json")], ""),
    ]


def step_list(work: str, out: str) -> list[tuple[int, str, list[str], str]]:
    return [
        (1, "features_base", ["features_base.py"], os.path.join(work, "features.parquet")),
        (2, "features_v8", ["features_v8.py"], os.path.join(work, "features_v8.parquet")),
        (3, "nowcast_h60", ["build_nowcast_features.py", "--horizon", "60"], os.path.join(work, "nowcast_h60.parquet")),
        (4, "network_h60", ["build_network_nowcast.py", "--horizon", "60"], os.path.join(work, "network_h60_v2.parquet")),
        (5, "train_final_v14", ["train_final_v14.py"], ""),
        (6, "audit_leakage", ["audit_leakage.py", "--report", os.path.join(out, "leakage_audit_v14.json")], ""),
        (7, "variable_dictionary", ["make_variable_dictionary.py", "--out", out], ""),
    ]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=os.path.join(ROOT, "data", "warehouse.duckdb"))
    p.add_argument("--work", default=os.path.join(ROOT, "work"))
    p.add_argument("--out", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--weather", default=os.path.join(ROOT, "data", "weather_daily.parquet"))
    p.add_argument("--from-step", type=int, default=1)
    p.add_argument("--to-step", type=int, default=7)
    p.add_argument("--force", action="store_true")
    p.add_argument("--version", choices=["v14", "v15"], default="v15")
    a = p.parse_args()
    for path, what in ((a.db, "DuckDB warehouse"), (a.weather, "weather parquet")):
        if not os.path.exists(path):
            print(f"ERROR: {what} not found: {path}", file=sys.stderr)
            return 2
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    env = dict(os.environ, BI_DUCKDB_PATH=os.path.abspath(a.db), BI_WORK_DIR=os.path.abspath(a.work),
               BI_OUT_DIR=os.path.abspath(a.out), BI_WEATHER_PATH=os.path.abspath(a.weather),
               BI_FEATURE_SPEC=os.path.join(os.path.abspath(a.out), "models", f"feature_spec_{a.version}.json"),
               BI_FLIGHT_SCOPE="all" if a.version == "v15" else "2022", PYTHONIOENCODING="utf-8")
    steps = step_list_v15(a.work, a.out) if a.version == "v15" else step_list(a.work, a.out)
    for num, name, cmd, output in steps:
        if num < a.from_step or num > a.to_step:
            continue
        if output and os.path.exists(output) and not a.force:
            print(f"[skip] step {num} {name}: {output} exists", flush=True)
            continue
        t0 = time.time()
        print(f"[run ] step {num} {name}", flush=True)
        rc = subprocess.call([sys.executable, "-u", os.path.join(SRC, cmd[0]), *cmd[1:]], env=env, cwd=SRC)
        if rc != 0:
            print(f"ERROR: step {num} {name} failed with exit code {rc}", file=sys.stderr)
            return rc
        print(f"[done] step {num} {name} in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
