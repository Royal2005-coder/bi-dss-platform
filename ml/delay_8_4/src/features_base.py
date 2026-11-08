# -*- coding: utf-8 -*-
"""Step 1 - base pre-event features (CRS schedule, calendar, expanding history <= D-1) from DuckDB marts.

Writes $BI_WORK_DIR/features.parquet. All history aggregates exclude the current date."""
import os

import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("BI_DUCKDB_PATH", os.path.join(ROOT, "data", "warehouse.duckdb"))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(ROOT, "work"))
os.makedirs(WORK, exist_ok=True)

BASE_SQL = """
SELECT
    f.flight_key, f.date_key, f.carrier_key, f.origin_airport_key,
    f.dest_airport_key, f.route_key, f.flight_number,
    f.scheduled_dep_time, f.scheduled_dep_hour, f.scheduled_dep_minute,
    f.crs_elapsed_time_minutes, f.distance_miles,
    f.arr_delay, f.is_arr_delayed15, f.is_completed, f.is_cancelled, f.is_diverted,
    d.full_date, d.year, d.quarter, d.month, d.day_of_month, d.day_of_year,
    d.week_of_year, d.weekday_number, d.is_weekend, d.season,
    c.carrier_code, c.carrier_name,
    o.airport_code AS origin, o.state_code AS origin_state,
    o.region AS origin_region, o.latitude AS origin_lat, o.longitude AS origin_lon,
    de.airport_code AS dest, de.state_code AS dest_state,
    de.region AS dest_region, de.latitude AS dest_lat, de.longitude AS dest_lon,
    r.distance_miles AS route_distance, r.distance_group, r.distance_group_order
FROM marts.fact_flight f
JOIN marts.dim_date d USING (date_key)
JOIN marts.dim_carrier c USING (carrier_key)
JOIN marts.dim_airport o ON f.origin_airport_key = o.airport_key
JOIN marts.dim_airport de ON f.dest_airport_key = de.airport_key
JOIN marts.dim_route r USING (route_key)
ORDER BY f.date_key, f.flight_key
"""


def load_base():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute(BASE_SQL).fetchdf()
    con.close()
    df["full_date"] = pd.to_datetime(df["full_date"])
    df["target"] = np.where(
        (df["is_completed"] == 1) & df["arr_delay"].notna(),
        df["is_arr_delayed15"],
        np.nan,
    )
    return df


def add_calendar_and_schedule(df):
    df = df.copy()
    df["is_weekend"] = df["is_weekend"].astype(int)
    df["dep_minute_of_day"] = df["scheduled_dep_hour"] * 60 + df["scheduled_dep_minute"]
    df["sched_arr_minute_of_day"] = (
        df["dep_minute_of_day"] + df["crs_elapsed_time_minutes"]
    ) % 1440
    df["scheduled_arr_hour"] = (df["sched_arr_minute_of_day"] // 60).astype("float")
    df["crs_elapsed_missing"] = df["crs_elapsed_time_minutes"].isna().astype(int)
    df["crs_elapsed_time_minutes"] = df["crs_elapsed_time_minutes"].fillna(-1)
    df["is_red_eye"] = ((df["scheduled_dep_hour"] >= 22) | (df["scheduled_dep_hour"] < 6)).astype(int)
    df["is_morning_peak"] = df["scheduled_dep_hour"].between(6, 9).astype(int)
    df["is_evening_peak"] = df["scheduled_dep_hour"].between(16, 20).astype(int)
    df["same_state"] = (df["origin_state"].fillna("") == df["dest_state"].fillna("")).astype(int)
    for col, period in (
        ("month_sin", (2 * np.pi, 12)),
        ("month_cos", (2 * np.pi, 12)),
        ("doy_sin", (2 * np.pi, 365.25)),
        ("doy_cos", (2 * np.pi, 365.25)),
        ("weekday_sin", (2 * np.pi, 7)),
        ("weekday_cos", (2 * np.pi, 7)),
        ("dep_hour_sin", (2 * np.pi, 24)),
        ("dep_hour_cos", (2 * np.pi, 24)),
    ):
        df[col] = np.nan
    df["month_sin"] = np.sin(2 * np.pi * (df["month"] - 1) / 12)
    df["month_cos"] = np.cos(2 * np.pi * (df["month"] - 1) / 12)
    df["doy_sin"] = np.sin(2 * np.pi * (df["day_of_year"] - 1) / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * (df["day_of_year"] - 1) / 365.25)
    df["weekday_sin"] = np.sin(2 * np.pi * (df["weekday_number"] - 1) / 7)
    df["weekday_cos"] = np.cos(2 * np.pi * (df["weekday_number"] - 1) / 7)
    df["dep_hour_sin"] = np.sin(2 * np.pi * df["scheduled_dep_hour"] / 24)
    df["dep_hour_cos"] = np.cos(2 * np.pi * df["scheduled_dep_hour"] / 24)
    for col in ("origin_state", "dest_state"):
        df[col] = df[col].fillna("UNK").replace("", "UNK")
    return df


def add_schedule_density(df):
    df = df.copy()
    specs = [
        (["date_key", "origin"], "origin_day_flights"),
        (["date_key", "origin", "scheduled_dep_hour"], "origin_hour_flights"),
        (["date_key", "dest"], "dest_day_flights"),
        (["date_key", "route_key"], "route_day_flights"),
        (["date_key", "carrier_code"], "carrier_day_flights"),
    ]
    for keys, name in specs:
        counts = df.groupby(keys, dropna=False).size().rename(name).reset_index()
        df = df.merge(counts, on=keys, how="left")
    return df


def add_prior_rate(df, keys, name, alpha=50.0):
    """Expanding prior target rate by date; current date is excluded."""
    labeled = df[df["target"].notna()].copy()
    daily = (
        labeled.groupby(keys + ["date_key"], dropna=False)
        .agg(n=("target", "size"), d=("target", "sum"))
        .reset_index()
    )
    daily = daily.sort_values(keys + ["date_key"])
    g = daily.groupby(keys, sort=False, dropna=False)
    daily["cum_n"] = g["n"].cumsum() - daily["n"]
    daily["cum_d"] = g["d"].cumsum() - daily["d"]

    global_daily = (
        labeled.groupby("date_key").agg(gn=("target", "size"), gd=("target", "sum"))
        .reset_index()
        .sort_values("date_key")
    )
    global_daily["gprior_n"] = global_daily["gn"].cumsum() - global_daily["gn"]
    global_daily["gprior_d"] = global_daily["gd"].cumsum() - global_daily["gd"]
    global_daily["global_prior"] = (global_daily["gprior_d"] + alpha * 0.2) / (
        global_daily["gprior_n"] + alpha
    )
    daily = daily.merge(global_daily[["date_key", "global_prior"]], on="date_key", how="left")
    daily[name] = (daily["cum_d"] + alpha * daily["global_prior"]) / (daily["cum_n"] + alpha)
    daily[name + "_n"] = daily["cum_n"]

    out = daily[keys + ["date_key", name, name + "_n"]]
    df = df.merge(out, on=keys + ["date_key"], how="left")
    df[name] = df[name].fillna(0.2)
    df[name + "_n"] = df[name + "_n"].fillna(0)
    return df


def add_prior_lag(df, keys, prefix):
    """Previous-day aggregate for the same key set; current day is excluded."""
    labeled = df[df["target"].notna()].copy()
    daily = (
        labeled.groupby(keys + ["date_key"], dropna=False)
        .agg(arr_mean=("arr_delay", "mean"), delayed=("target", "mean"), n=("target", "size"))
        .reset_index()
    )
    daily["dt"] = pd.to_datetime(daily["date_key"].astype(str), format="%Y%m%d")
    daily = daily.sort_values(keys + ["dt"])
    g = daily.groupby(keys, sort=False, dropna=False)
    daily[f"{prefix}_arr_mean"] = g["arr_mean"].shift(1)
    daily[f"{prefix}_delayed"] = g["delayed"].shift(1)
    daily[f"{prefix}_n"] = g["n"].shift(1)
    daily[f"{prefix}_prev_dt"] = g["dt"].shift(1)
    daily[f"{prefix}_days"] = (daily["dt"] - daily[f"{prefix}_prev_dt"]).dt.days
    out = daily[
        keys
        + [
            "date_key",
            f"{prefix}_arr_mean",
            f"{prefix}_delayed",
            f"{prefix}_n",
            f"{prefix}_days",
        ]
    ]
    return df.merge(out, on=keys + ["date_key"], how="left")


def build_features(df):
    df = add_calendar_and_schedule(df)
    df = add_schedule_density(df)
    df = add_prior_rate(df, ["carrier_code"], "carrier_rate")
    df = add_prior_rate(df, ["origin"], "origin_rate")
    df = add_prior_rate(df, ["dest"], "dest_rate")
    df = add_prior_rate(df, ["route_key"], "route_rate")
    df = add_prior_rate(df, ["carrier_code", "route_key"], "carrier_route_rate")
    df = add_prior_rate(df, ["origin", "scheduled_dep_hour"], "origin_hour_rate")
    df = add_prior_rate(df, ["carrier_code", "flight_number"], "flight_number_rate")
    df = add_prior_lag(df, ["carrier_code", "flight_number"], "fn")
    df = add_prior_lag(
        df, ["carrier_code", "flight_number", "origin", "dest"], "route_fn"
    )
    return df


def main() -> None:
    df = build_features(load_base())
    out = os.path.join(WORK, "features.parquet")
    df.to_parquet(out, index=False)
    print("wrote", out, df.shape, "labelled", int(df["target"].notna().sum()), flush=True)


if __name__ == "__main__":
    main()
