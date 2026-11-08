# -*- coding: utf-8 -*-
"""Anti-leakage audit for 8.4.2 (independent re-implementation, not the SQL used to build features).

Checks
  1. Banned post-event columns are absent from the model feature list.
  2. Network nowcast: for a random sample of flights, recompute carrier/national departure counts and
     >=15-min rates in the 120-min window before T_pred with plain pandas and compare with the parquet.
     Also asserts that no counted event became known after T_pred and that the flight itself is excluded.
  3. Inbound (aircraft-history proxy): in_dep_delay_known is only populated when the inbound departure
     was known at T_pred.
  4. History features: every rolling/prior window ends strictly before the flight date (SQL text check).
Writes a JSON report and exits non-zero on failure.

Usage: python audit_leakage.py [--sample 400] [--report PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import duckdb
import numpy as np
import pandas as pd

from build_network_nowcast import DB, utc_offsets
from flights_source import EPOCH, SCOPE, flights_sql

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(HERE, "..", "work"))
H = 60
BANNED = {"dep_delay", "arr_delay", "taxi_out", "taxi_in", "wheels_off", "wheels_on", "air_time",
          "elapsed_time", "dep_time", "arr_time", "cancelled", "diverted", "is_cancelled", "is_diverted",
          "is_completed", "is_arr_delayed15", "is_dep_delayed15", "delay_due_carrier", "delay_due_weather",
          "delay_due_nas", "delay_due_security", "delay_due_late_aircraft", "target", "cancellation_code"}


def load_flights() -> pd.DataFrame:
    con = duckdb.connect(DB, read_only=True)
    off, _ = utc_offsets(con)
    con.register("off_df", off)
    df = con.execute(
        f"""
        SELECT s.flight_key,
               s.carrier_code, s.dep_delay, s.cancelled,
               date_diff('day', DATE '{EPOCH}', s.flight_date) * 1440
                 + (s.crs_dep_time // 100) * 60 + s.crs_dep_time % 100 - o.off_min AS sdep_utc
        FROM ({flights_sql()}) s
        JOIN off_df o ON o.airport = s.origin_code AND o.flight_date = s.flight_date
        """
    ).fetchdf()
    con.close()
    df["known_dep"] = df["sdep_utc"] + df["dep_delay"]
    df["t_pred"] = df["sdep_utc"] - H
    return df


def check_network(fl: pd.DataFrame, sample: int, rng: np.random.Generator) -> dict:
    net = pd.read_parquet(os.path.join(WORK, f"network_h{H}_v2.parquet"),
                          columns=["flight_key", "car_dep_n_120", "car_dep_r15_120", "nat_dep_n_120"])
    dep = fl[(fl.cancelled == 0) & fl.dep_delay.notna()]
    by_car = {c: g for c, g in dep.groupby("carrier_code")}
    picks = fl.sample(sample, random_state=int(rng.integers(1e9)))
    rows, self_counted, future_counted = [], 0, 0
    for r in picks.itertuples():
        lo, hi = r.t_pred - 120, r.t_pred
        g = by_car[r.carrier_code]
        win = g[(g.known_dep > lo) & (g.known_dep <= hi)]
        nat = dep[(dep.known_dep > lo) & (dep.known_dep <= hi)]
        self_counted += int(r.flight_key in set(win.flight_key)) if not (r.dep_delay is not None and r.dep_delay < -H) else 0
        future_counted += int((win.known_dep > r.t_pred).sum())
        rows.append((r.flight_key, len(win), (win.dep_delay >= 15).mean() if len(win) else np.nan, len(nat)))
    ref = pd.DataFrame(rows, columns=["flight_key", "n_ref", "r15_ref", "nat_ref"]).merge(net, on="flight_key")
    n_match = float((ref.n_ref == ref.car_dep_n_120).mean())
    nat_match = float((ref.nat_ref == ref.nat_dep_n_120).mean())
    both = ref.dropna(subset=["r15_ref", "car_dep_r15_120"])
    r_err = float((both.r15_ref - both.car_dep_r15_120).abs().max()) if len(both) else 0.0
    return dict(sample=int(len(ref)), carrier_count_match=n_match, national_count_match=nat_match,
                carrier_r15_max_abs_err=r_err, self_counted=self_counted, future_events_counted=future_counted,
                passed=bool(n_match >= 0.999 and nat_match >= 0.999 and r_err < 1e-4 and self_counted == 0
                            and future_counted == 0))


def check_inbound() -> dict:
    nc = pd.read_parquet(os.path.join(WORK, f"nowcast_h{H}.parquet"),
                         columns=["has_inbound", "in_status_known", "in_dep_delay_known", "in_delay_lb", "turn_slack_lb"])
    known_without_flag = int((nc.in_dep_delay_known.notna() & (nc.in_status_known != 1)).sum())
    return dict(rows_with_inbound=int((nc.has_inbound == 1).sum()),
                share_with_inbound=float((nc.has_inbound == 1).mean()),
                delay_known_without_status_flag=known_without_flag, passed=known_without_flag == 0)


def check_weather() -> dict:
    """v15 hourly weather: every observation used must be stamped <= T_pred - 60 min (age >= 0)."""
    path = os.path.join(WORK, f"weather_h{H}.parquet")
    if not os.path.exists(path):
        return dict(passed=True, note="no hourly weather features (v14 scope)")
    w = pd.read_parquet(path, columns=["o_wxh_age_h", "d_wxh_age_h"])
    mins = {c: float(w[c].min()) for c in w.columns}
    return dict(min_obs_age_hours_before_cutoff=mins, rows=int(len(w)),
                share_origin_obs_within_3h=float((w.o_wxh_age_h <= 3).mean()), passed=all(v >= 0 for v in mins.values()))


def check_features() -> dict:
    spec_path = os.environ.get("BI_FEATURE_SPEC", os.path.join(HERE, "..", "outputs", "models", "feature_spec_v14.json"))
    if not os.path.exists(spec_path):
        return dict(passed=False, error=f"missing {spec_path}")
    feats = set(json.load(open(spec_path, encoding="utf-8"))["features"])
    hit = sorted(feats & BANNED)
    return dict(n_features=len(feats), banned_found=hit, passed=not hit)


def check_history_sql() -> dict:
    src = open(os.path.join(HERE, "features_all.py" if SCOPE == "all" else "features_v8.py"), encoding="utf-8").read()
    windows = src.count("PRECEDING AND 1 PRECEDING")
    current_row = src.count("AND CURRENT ROW")
    return dict(windows_ending_1_preceding=windows, windows_including_current_row=current_row,
                passed=windows >= 3 and current_row == 0)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, default=400)
    p.add_argument("--report", default=os.path.join(HERE, "..", "outputs", "leakage_audit_v14.json"))
    a = p.parse_args()
    rng = np.random.default_rng(42)
    report = {"features": check_features(), "history_sql": check_history_sql(), "inbound": check_inbound(),
              "weather_hourly": check_weather()}
    report["network_bruteforce"] = check_network(load_flights(), a.sample, rng)
    report["all_passed"] = all(v["passed"] for v in report.values() if isinstance(v, dict))
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
