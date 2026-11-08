# -*- coding: utf-8 -*-
"""v15 step 1 - base + history features for 2019-01..2023-08 in pure DuckDB SQL (memory-safe for 3M rows).

Same semantics and names as features_base.py + features_v8.py (v14) minus the daily perfect-forecast weather:
  * schedule / calendar / airport attributes (known when the CRS schedule is published)
  * schedule density counts of the flight day (published timetable)
  * rolling delay rates by 14 groups: previous data-day, last 7 / 30 data-days, smoothed expanding prior;
    every window ends at 1 PRECEDING => strictly before the flight date. History now starts in 2019.
  * previous operation of the same flight number (and same flight number + route), strictly before D.
Writes $BI_WORK_DIR/features_all.parquet.
"""
from __future__ import annotations

import os
import time

import duckdb

from flights_source import flights_sql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("BI_DUCKDB_PATH", os.path.join(ROOT, "data", "warehouse.duckdb"))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(ROOT, "work"))
GROUPS = {
    "carrier": ["carrier_code"], "origin": ["origin"], "dest": ["dest"], "route": ["route"],
    "carrier_route": ["carrier_code", "route"], "origin_hour": ["origin", "scheduled_dep_hour"],
    "dest_hour": ["dest", "scheduled_arr_hour"], "carrier_hour": ["carrier_code", "scheduled_dep_hour"],
    "carrier_origin": ["carrier_code", "origin"], "carrier_dest": ["carrier_code", "dest"],
    "origin_month": ["origin", "month"], "carrier_month": ["carrier_code", "month"],
    "origin_weekday": ["origin", "weekday_number"], "carrier_weekday": ["carrier_code", "weekday_number"],
}
PRIOR_RATE, PRIOR_N = 0.20, 20


def build(out_path: str) -> None:
    t0 = time.time()
    con = duckdb.connect(DB, read_only=True)
    con.execute("SET threads TO 8")
    con.execute(f"SET temp_directory = '{os.path.join(WORK, 'duckdb_tmp').replace(os.sep, '/')}'")
    con.execute(f"""
        CREATE TEMP TABLE b AS
        WITH s AS ({flights_sql()})
        SELECT s.flight_key, s.flight_date,
               CAST(strftime(s.flight_date, '%Y%m%d') AS INTEGER) AS date_key,
               s.carrier_code, s.flight_number, s.origin_code AS origin, s.dest_code AS dest,
               s.origin_code || '-' || s.dest_code AS route,
               CASE WHEN s.crs_dep_time = 2400 THEN 0 ELSE s.crs_dep_time END AS scheduled_dep_time,
               (CASE WHEN s.crs_dep_time = 2400 THEN 0 ELSE s.crs_dep_time END) // 100 AS scheduled_dep_hour,
               s.crs_dep_time % 100 AS scheduled_dep_minute,
               COALESCE(s.crs_elapsed_time, -1) AS crs_elapsed_time_minutes,
               CAST(s.crs_elapsed_time IS NULL AS INTEGER) AS crs_elapsed_missing,
               s.distance AS distance_miles,
               LEAST(CAST(s.distance // 250 AS INTEGER) + 1, 11) AS distance_group_order,
               'G' || LPAD(CAST(LEAST(CAST(s.distance // 250 AS INTEGER) + 1, 11) AS VARCHAR), 2, '0') AS distance_group,
               year(s.flight_date) AS year, quarter(s.flight_date) AS quarter, month(s.flight_date) AS month,
               day(s.flight_date) AS day_of_month, dayofyear(s.flight_date) AS day_of_year,
               weekofyear(s.flight_date) AS week_of_year, isodow(s.flight_date) AS weekday_number,
               CAST(isodow(s.flight_date) >= 6 AS INTEGER) AS is_weekend,
               CASE WHEN month(s.flight_date) IN (12, 1, 2) THEN 'Winter' WHEN month(s.flight_date) IN (3, 4, 5) THEN 'Spring'
                    WHEN month(s.flight_date) IN (6, 7, 8) THEN 'Summer' ELSE 'Autumn' END AS season,
               COALESCE(o.state_code, 'UNK') AS origin_state, COALESCE(d.state_code, 'UNK') AS dest_state,
               COALESCE(o.region, 'UNK') AS origin_region, COALESCE(d.region, 'UNK') AS dest_region,
               o.latitude AS origin_lat, o.longitude AS origin_lon, d.latitude AS dest_lat, d.longitude AS dest_lon,
               CAST(COALESCE(o.state_code, '') = COALESCE(d.state_code, '') AS INTEGER) AS same_state,
               CASE WHEN s.cancelled = 0 AND s.diverted = 0 AND s.arr_delay IS NOT NULL
                    THEN CAST(s.arr_delay >= 15 AS DOUBLE) END AS target,
               s.arr_delay, s.cancelled AS is_cancelled, s.diverted AS is_diverted
        FROM s
        LEFT JOIN seeds.airport_master o ON o.airport_code = s.origin_code
        LEFT JOIN seeds.airport_master d ON d.airport_code = s.dest_code
    """)
    con.execute("""
        CREATE TEMP TABLE b2 AS
        SELECT *,
            scheduled_dep_hour * 60 + scheduled_dep_minute AS dep_minute_of_day,
            CASE WHEN crs_elapsed_time_minutes >= 0
                 THEN (scheduled_dep_hour * 60 + scheduled_dep_minute + crs_elapsed_time_minutes) % 1440 END AS sched_arr_minute_of_day,
            CASE WHEN crs_elapsed_time_minutes >= 0
                 THEN ((scheduled_dep_hour * 60 + scheduled_dep_minute + crs_elapsed_time_minutes) % 1440) // 60 END AS scheduled_arr_hour,
            CAST(scheduled_dep_hour >= 22 OR scheduled_dep_hour < 6 AS INTEGER) AS is_red_eye,
            CAST(scheduled_dep_hour BETWEEN 6 AND 9 AS INTEGER) AS is_morning_peak,
            CAST(scheduled_dep_hour BETWEEN 16 AND 20 AS INTEGER) AS is_evening_peak,
            sin(2 * pi() * (month - 1) / 12) AS month_sin, cos(2 * pi() * (month - 1) / 12) AS month_cos,
            sin(2 * pi() * (day_of_year - 1) / 365.25) AS doy_sin, cos(2 * pi() * (day_of_year - 1) / 365.25) AS doy_cos,
            sin(2 * pi() * (weekday_number - 1) / 7) AS weekday_sin, cos(2 * pi() * (weekday_number - 1) / 7) AS weekday_cos,
            sin(2 * pi() * scheduled_dep_hour / 24) AS dep_hour_sin, cos(2 * pi() * scheduled_dep_hour / 24) AS dep_hour_cos,
            distance_miles / NULLIF(crs_elapsed_time_minutes, 0) AS pace_mph,
            COUNT(*) OVER (PARTITION BY date_key, origin) AS origin_day_flights,
            COUNT(*) OVER (PARTITION BY date_key, origin, scheduled_dep_hour) AS origin_hour_flights,
            COUNT(*) OVER (PARTITION BY date_key, dest) AS dest_day_flights,
            COUNT(*) OVER (PARTITION BY date_key, route) AS route_day_flights,
            COUNT(*) OVER (PARTITION BY date_key, carrier_code) AS carrier_day_flights
        FROM b
    """)
    con.execute("DROP TABLE b")
    con.execute("""
        CREATE TEMP TABLE b3 AS
        SELECT *, COUNT(*) OVER (PARTITION BY date_key, dest, scheduled_arr_hour) AS dest_arr_in_hour,
               origin_hour_flights AS origin_dep_in_hour
        FROM b2
    """)
    con.execute("DROP TABLE b2")

    sels, joins = ["b3.*"], []
    for name, keys in GROUPS.items():
        k = ", ".join(keys)
        con.execute(f"""
            CREATE TEMP TABLE r_{name} AS
            WITH daily AS (SELECT {k}, date_key, AVG(target) AS rate, COUNT(*) AS n, SUM(target) AS pos
                           FROM b3 WHERE target IS NOT NULL GROUP BY {k}, date_key)
            SELECT {k}, date_key,
              LAG(rate) OVER w AS {name}_prev_day,
              SUM(pos) OVER w7 / NULLIF(SUM(n) OVER w7, 0) AS {name}_7d,
              SUM(pos) OVER w30 / NULLIF(SUM(n) OVER w30, 0) AS {name}_30d,
              (COALESCE(SUM(pos) OVER wall, 0) + {PRIOR_RATE * PRIOR_N}) / (COALESCE(SUM(n) OVER wall, 0) + {PRIOR_N}) AS {name}_prior
            FROM daily
            WINDOW w AS (PARTITION BY {k} ORDER BY date_key),
                   w7 AS (PARTITION BY {k} ORDER BY date_key ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING),
                   w30 AS (PARTITION BY {k} ORDER BY date_key ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING),
                   wall AS (PARTITION BY {k} ORDER BY date_key ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
        """)
        cond = " AND ".join(f"b3.{c} IS NOT DISTINCT FROM r_{name}.{c}" for c in keys)
        joins.append(f"LEFT JOIN r_{name} ON {cond} AND b3.date_key = r_{name}.date_key")
        sels.append(f"r_{name}.{name}_prev_day, r_{name}.{name}_7d, r_{name}.{name}_30d, r_{name}.{name}_prior")
    for prefix, keys in (("fn", ["carrier_code", "flight_number"]),
                         ("route_fn", ["carrier_code", "flight_number", "origin", "dest"])):
        k = ", ".join(keys)
        con.execute(f"""
            CREATE TEMP TABLE p_{prefix} AS
            WITH daily AS (SELECT {k}, date_key, flight_date, AVG(arr_delay) AS arr_mean, AVG(target) AS delayed,
                                  COUNT(*) AS n
                           FROM b3 WHERE target IS NOT NULL GROUP BY {k}, date_key, flight_date)
            SELECT {k}, date_key,
                   LAG(arr_mean) OVER w AS {prefix}_arr_mean, LAG(delayed) OVER w AS {prefix}_delayed,
                   LAG(n) OVER w AS {prefix}_n, date_diff('day', LAG(flight_date) OVER w, flight_date) AS {prefix}_days
            FROM daily WINDOW w AS (PARTITION BY {k} ORDER BY date_key)
        """)
        cond = " AND ".join(f"b3.{c} = p_{prefix}.{c}" for c in keys)
        joins.append(f"LEFT JOIN p_{prefix} ON {cond} AND b3.date_key = p_{prefix}.date_key")
        sels.append(f"p_{prefix}.{prefix}_arr_mean, p_{prefix}.{prefix}_delayed, p_{prefix}.{prefix}_n, p_{prefix}.{prefix}_days")
    con.execute(f"""
        COPY (SELECT {', '.join(sels)} FROM b3 {' '.join(joins)} ORDER BY b3.flight_date, b3.flight_key)
        TO '{out_path}' (FORMAT PARQUET)
    """)
    n = con.execute(f"SELECT COUNT(*), COUNT(target), MIN(date_key), MAX(date_key) FROM read_parquet('{out_path}')").fetchone()
    print(f"wrote {out_path} rows={n[0]} labelled={n[1]} dates={n[2]}..{n[3]} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    os.makedirs(WORK, exist_ok=True)
    build(os.path.join(WORK, "features_all.parquet").replace(os.sep, "/"))
