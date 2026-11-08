# -*- coding: utf-8 -*-
"""Fetch daily airport weather (pre-event context) from Open-Meteo archive."""
import json
import os
import sys
import time

import duckdb
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("BI_DUCKDB_PATH", os.path.join(ROOT, "data", "warehouse.duckdb"))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(ROOT, "work"))
OUT = os.environ.get("BI_WEATHER_PATH", os.path.join(ROOT, "data", "weather_daily.parquet"))
CHECK = os.path.join(WORK, "weather_daily_checkpoint.parquet")

START = "2022-01-01"
END = "2023-08-31"
BATCH = 10
FIELDS = "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m,visibility,cloud_cover,weather_code"


def fetch_batch(coords):
    params = {
        "latitude": ",".join(str(x[1]) for x in coords),
        "longitude": ",".join(str(x[2]) for x in coords),
        "start_date": START,
        "end_date": END,
        "hourly": FIELDS,
        "timezone": "auto",
    }
    for attempt in range(4):
        try:
            r = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params=params,
                timeout=120,
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = [data]
            if len(data) != len(coords):
                raise ValueError(f"expected {len(coords)} locations, got {len(data)}")
            return data
        except Exception as exc:
            print("retry", attempt + 1, exc, flush=True)
            time.sleep(60 * (attempt + 1))
    raise RuntimeError("weather batch failed")


def aggregate(airport, obj):
    hourly = obj.get("hourly") or {}
    if not hourly.get("time"):
        return None
    df = pd.DataFrame(hourly)
    df["time"] = pd.to_datetime(df["time"])
    df["date_key"] = df["time"].dt.strftime("%Y%m%d").astype(int)
    df["bad"] = (
        (df["precipitation"].fillna(0) > 0.5)
        | (df["visibility"].fillna(99999) < 5000)
        | (df["wind_speed_10m"].fillna(0) > 40)
        | (df["weather_code"].fillna(0) >= 51)
    ).astype(int)
    daily = (
        df.groupby("date_key")
        .agg(
            precip_sum=("precipitation", "sum"),
            precip_max=("precipitation", "max"),
            wind_max=("wind_speed_10m", "max"),
            gust_max=("wind_gusts_10m", "max"),
            visibility_min=("visibility", "min"),
            cloud_mean=("cloud_cover", "mean"),
            temp_mean=("temperature_2m", "mean"),
            weather_code_max=("weather_code", "max"),
            bad_hours=("bad", "sum"),
        )
        .reset_index()
    )
    daily.insert(0, "airport_code", airport)
    daily["bad_hours"] = daily["bad_hours"].clip(lower=0, upper=24)
    return daily


def main():
    con = duckdb.connect(DB, read_only=True)
    airports = con.execute(
        "SELECT airport_code, latitude, longitude FROM marts.dim_airport "
        "ORDER BY airport_code"
    ).fetchdf()
    con.close()
    rows = [
        (r.airport_code, round(float(r.latitude), 4), round(float(r.longitude), 4))
        for r in airports.itertuples()
    ]
    print("airports", len(rows), flush=True)
    frames = []
    done = set()
    if os.path.exists(CHECK):
        existing = pd.read_parquet(CHECK)
        frames.append(existing)
        done = set(existing["airport_code"].astype(str))
        rows = [r for r in rows if r[0] not in done]
        print("resume; already", len(done), "remaining", len(rows), flush=True)
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        print("batch", i, i + len(batch), flush=True)
        data = fetch_batch(batch)
        for (code, _, _), obj in zip(batch, data):
            daily = aggregate(code, obj)
            if daily is not None:
                frames.append(daily)
        # checkpoint after each batch
        pd.concat(frames, ignore_index=True).to_parquet(CHECK, index=False)
        time.sleep(30)
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(OUT, index=False)
    print("saved", OUT, out.shape, flush=True)
    print(out.head().to_string(), flush=True)


if __name__ == "__main__":
    main()
