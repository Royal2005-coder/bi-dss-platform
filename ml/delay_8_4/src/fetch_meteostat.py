# -*- coding: utf-8 -*-
"""Hourly observed weather (UTC) per airport from Meteostat bulk data, 2019-01-01..2023-09-01.

Airport -> station: ICAO match first (K+IATA, or listed ICAO), else nearest station <= 30 km with hourly
inventory. Output: data/weather_hourly_utc.parquet (airport, ts_utc, temp, dwpt, rhum, prcp, snow, wspd,
wpgt, pres, coco, station, dist_km). Licence: Meteostat CC BY-NC 4.0 (academic use).
Usage: python fetch_meteostat.py
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("BI_DUCKDB_PATH", os.path.join(ROOT, "data", "warehouse.duckdb"))
OUT = os.environ.get("BI_WEATHER_HOURLY", os.path.join(ROOT, "data", "weather_hourly_utc.parquet"))
COLS = ["date", "hour", "temp", "dwpt", "rhum", "prcp", "snow", "wdir", "wspd", "wpgt", "pres", "tsun", "coco"]
KEEP = ["temp", "dwpt", "rhum", "prcp", "snow", "wspd", "wpgt", "pres", "coco"]
START, END = "2019-01-01", "2023-09-01"
MAX_KM = 30.0


def haversine(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def airports() -> pd.DataFrame:
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("""
        SELECT a.airport_code, a.latitude, a.longitude FROM seeds.airport_master a
        WHERE a.airport_code IN (SELECT origin_code FROM staging.stg_flights UNION SELECT dest_code FROM staging.stg_flights)
          AND a.latitude IS NOT NULL""").fetchdf()
    con.close()
    return df


def match_stations(ap: pd.DataFrame) -> pd.DataFrame:
    raw = requests.get("https://bulk.meteostat.net/v2/stations/lite.json.gz", timeout=120).content
    def covers(s: dict) -> bool:
        h = (s.get("inventory") or {}).get("hourly") or {}
        return bool(h.get("start") and h.get("end")) and h["start"] <= "2019-12-31" and h["end"] >= "2023-08-31"
    st = [s for s in json.load(gzip.open(io.BytesIO(raw))) if covers(s) and s.get("location")]
    by_icao = {s["identifiers"].get("icao"): s for s in st if s["identifiers"].get("icao")}
    rows = []
    for r in ap.itertuples():
        cands = [by_icao.get(c) for c in (f"K{r.airport_code}", f"P{r.airport_code[1:]}" if len(r.airport_code) == 3 else None)]
        best = None
        for s in [c for c in cands if c]:
            d = haversine(r.latitude, r.longitude, s["location"]["latitude"], s["location"]["longitude"])
            if d <= MAX_KM:
                best = (s, d)
                break
        if best is None:
            dists = [(s, haversine(r.latitude, r.longitude, s["location"]["latitude"], s["location"]["longitude"]))
                     for s in st if abs(s["location"]["latitude"] - r.latitude) < 0.5]
            if dists:
                s, d = min(dists, key=lambda t: t[1])
                if d <= MAX_KM:
                    best = (s, d)
        rows.append({"airport": r.airport_code, "station": best[0]["id"] if best else None,
                     "dist_km": round(best[1], 1) if best else None})
    return pd.DataFrame(rows)


def fetch(station: str) -> pd.DataFrame | None:
    for _ in range(3):
        try:
            r = requests.get(f"https://bulk.meteostat.net/v2/hourly/{station}.csv.gz", timeout=180)
            if r.status_code != 200:
                return None
            df = pd.read_csv(io.BytesIO(r.content), compression="gzip", header=None, names=COLS)
            df = df[(df.date >= START) & (df.date < END)]
            df["ts_utc"] = pd.to_datetime(df.date) + pd.to_timedelta(df.hour, unit="h")
            return df[["ts_utc"] + KEEP].astype({c: "float32" for c in KEEP})
        except Exception:  # noqa: BLE001 - network retry
            continue
    return None


def main() -> None:
    m = match_stations(airports())
    print(f"airports={len(m)} matched={m.station.notna().sum()} median_km={m.dist_km.median()}", flush=True)
    stations = m.station.dropna().unique().tolist()
    with ThreadPoolExecutor(8) as ex:
        data = dict(zip(stations, ex.map(fetch, stations)))
    parts = []
    for r in m.dropna(subset=["station"]).itertuples():
        d = data.get(r.station)
        if d is not None and len(d):
            parts.append(d.assign(airport=r.airport, station=r.station, dist_km=r.dist_km))
    out = pd.concat(parts, ignore_index=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_parquet(OUT, index=False)
    m.to_csv(OUT.replace(".parquet", "_stations.csv"), index=False)
    print(f"wrote {OUT} rows={len(out)} airports_with_data={out.airport.nunique()}", flush=True)


if __name__ == "__main__":
    main()
