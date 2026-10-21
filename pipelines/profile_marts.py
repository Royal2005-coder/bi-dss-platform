#!/usr/bin/env python3
import json
import os

import duckdb

DB = os.environ.get("BI_DUCKDB_PATH", "/data/warehouse/warehouse.duckdb")
OUT = os.environ.get("BI_MART_PROFILE", "/workspace/out/mart_profile.json")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

con = duckdb.connect(DB, read_only=True)


def rows(sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(sql):
    return rows(sql)[0]


tables = ["dim_date", "dim_carrier", "dim_airport", "dim_route",
          "dim_delay_reason", "dim_flight_status", "fact_flight", "fact_delay_reason"]
row_counts = {t: con.execute(f"SELECT COUNT(*) FROM marts.{t}").fetchone()[0] for t in tables}

status = rows("""
    SELECT s.flight_status_code, s.flight_status_name, s.sort_order, COUNT(*) AS flights,
           ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS share_pct
    FROM marts.fact_flight f
    JOIN marts.dim_flight_status s ON f.flight_status_key = s.flight_status_key
    GROUP BY 1, 2, 3 ORDER BY 3
""")
status_by_year = rows("""
    SELECT d.year, s.flight_status_code, COUNT(*) AS flights
    FROM marts.fact_flight f
    JOIN marts.dim_flight_status s ON f.flight_status_key = s.flight_status_key
    JOIN marts.dim_date d ON f.date_key = d.date_key
    GROUP BY 1, 2 ORDER BY 1, 2
""")
year_counts = rows("""
    SELECT d.year, COUNT(*) AS flights,
           SUM(f.is_cancelled) AS cancelled,
           SUM(f.is_diverted) AS diverted,
           SUM(f.is_arr_delayed15) AS delayed15,
           ROUND(AVG(f.arr_delay), 2) AS avg_arr_delay
    FROM marts.fact_flight f
    JOIN marts.dim_date d ON f.date_key = d.date_key
    GROUP BY 1 ORDER BY 1
""")
cancellation = rows("""
    SELECT COALESCE(f.cancellation_code, '(null)') AS code, COUNT(*) AS n
    FROM marts.fact_flight f WHERE f.is_cancelled = 1
    GROUP BY 1 ORDER BY 2 DESC
""")
delay_reasons = rows("""
    SELECT r.delay_reason_key, r.delay_reason_code, r.delay_reason_name, r.sort_order,
           COUNT(*) AS rows, SUM(f.delay_minutes) AS minutes,
           ROUND(AVG(f.delay_minutes), 2) AS avg_minutes,
           ROUND(100.0 * SUM(f.delay_minutes) / SUM(SUM(f.delay_minutes)) OVER (), 2) AS share_pct
    FROM marts.fact_delay_reason f
    JOIN marts.dim_delay_reason r ON f.delay_reason_key = r.delay_reason_key
    GROUP BY 1, 2, 3, 4 ORDER BY 4
""")
delay_reasons_by_year = rows("""
    SELECT d.year, r.delay_reason_code, COUNT(*) AS rows, SUM(f.delay_minutes) AS minutes
    FROM marts.fact_delay_reason f
    JOIN marts.dim_delay_reason r ON f.delay_reason_key = r.delay_reason_key
    JOIN marts.dim_date d ON f.date_key = d.date_key
    GROUP BY 1, 2 ORDER BY 1, 2
""")
reasons_per_flight = rows("""
    SELECT n_reasons, COUNT(*) AS flights
    FROM (
        SELECT f.flight_key, COUNT(*) AS n_reasons
        FROM marts.fact_delay_reason f
        GROUP BY 1
    ) GROUP BY 1 ORDER BY 1
""")
delay_totals = one("""
    SELECT COUNT(*) AS rows, SUM(delay_minutes) AS minutes, ROUND(AVG(delay_minutes), 2) AS avg_minutes,
           MIN(delay_minutes) AS min_minutes, MAX(delay_minutes) AS max_minutes
    FROM marts.fact_delay_reason
""")
flight_metrics = one("""
    SELECT COUNT(*) AS flights,
           SUM(is_cancelled) AS cancelled,
           SUM(is_diverted) AS diverted,
           SUM(is_completed) AS completed,
           SUM(is_arr_delayed15) AS delayed15,
           SUM(has_delay_reason) AS with_reason,
           ROUND(AVG(arr_delay), 2) AS avg_arr_delay,
           ROUND(quantile_cont(arr_delay, 0.5), 2) AS median_arr_delay,
           ROUND(AVG(CASE WHEN is_arr_delayed15 = 1 THEN arr_delay END), 2) AS avg_delayed_arr_delay,
           ROUND(AVG(dep_delay), 2) AS avg_dep_delay
    FROM marts.fact_flight
""")
delayed_minutes = one("""
    SELECT ROUND(SUM(arr_delay_minutes), 0) AS arr_delay_minutes,
           ROUND(AVG(arr_delay_minutes), 2) AS avg_arr_delay_minutes
    FROM marts.fact_flight WHERE is_arr_delayed15 = 1
""")
top_carriers = rows("""
    SELECT c.carrier_code, c.carrier_name, COUNT(*) AS flights,
           SUM(f.is_arr_delayed15) AS delayed15,
           SUM(f.is_cancelled) AS cancelled,
           ROUND(100.0 * SUM(f.is_arr_delayed15) / NULLIF(SUM(f.is_completed), 0), 2) AS delayed15_pct,
           ROUND(100.0 * SUM(f.is_cancelled) / COUNT(*), 2) AS cancelled_pct
    FROM marts.fact_flight f
    JOIN marts.dim_carrier c ON f.carrier_key = c.carrier_key
    GROUP BY 1, 2 ORDER BY flights DESC
""")
top_origins = rows("""
    SELECT a.airport_code, a.airport_name, a.city, a.state_code, a.region, COUNT(*) AS flights,
           ROUND(100.0 * SUM(f.is_arr_delayed15) / NULLIF(SUM(f.is_completed), 0), 2) AS delayed15_pct,
           ROUND(100.0 * SUM(f.is_cancelled) / COUNT(*), 2) AS cancelled_pct
    FROM marts.fact_flight f
    JOIN marts.dim_airport a ON f.origin_airport_key = a.airport_key
    GROUP BY 1, 2, 3, 4, 5 ORDER BY flights DESC LIMIT 12
""")
regions = rows("""
    SELECT a.region, COUNT(*) AS flights,
           ROUND(100.0 * SUM(f.is_arr_delayed15) / NULLIF(SUM(f.is_completed), 0), 2) AS delayed15_pct,
           ROUND(100.0 * SUM(f.is_cancelled) / COUNT(*), 2) AS cancelled_pct
    FROM marts.fact_flight f
    JOIN marts.dim_airport a ON f.origin_airport_key = a.airport_key
    GROUP BY 1 ORDER BY 2 DESC
""")
distance_groups = rows("""
    SELECT r.distance_group, r.distance_group_order, COUNT(*) AS flights,
           ROUND(AVG(f.arr_delay), 2) AS avg_arr_delay,
           ROUND(100.0 * SUM(f.is_arr_delayed15) / NULLIF(SUM(f.is_completed), 0), 2) AS delayed15_pct
    FROM marts.fact_flight f
    JOIN marts.dim_route r ON f.route_key = r.route_key
    GROUP BY 1, 2 ORDER BY 2
""")
airports_by_region = rows("""
    SELECT region, COUNT(*) AS airports FROM marts.dim_airport GROUP BY 1 ORDER BY 2 DESC
""")
airports_by_state = rows("""
    SELECT state_code, state_name, COUNT(*) AS airports FROM marts.dim_airport
    GROUP BY 1, 2 ORDER BY 3 DESC, 1 LIMIT 12
""")
date_months = rows("""
    SELECT year, COUNT(*) AS days, SUM(CASE WHEN is_weekend THEN 1 ELSE 0 END) AS weekend_days
    FROM marts.dim_date GROUP BY 1 ORDER BY 1
""")
samples = {
    "dim_date": rows("SELECT * FROM marts.dim_date ORDER BY date_key LIMIT 3"),
    "dim_carrier": rows("SELECT * FROM marts.dim_carrier ORDER BY carrier_key LIMIT 5"),
    "dim_airport": rows("SELECT * FROM marts.dim_airport ORDER BY airport_key LIMIT 5"),
    "dim_route": rows("SELECT * FROM marts.dim_route ORDER BY route_key LIMIT 5"),
    "dim_delay_reason": rows("SELECT * FROM marts.dim_delay_reason ORDER BY delay_reason_key"),
    "dim_flight_status": rows("SELECT * FROM marts.dim_flight_status ORDER BY flight_status_key"),
    "fact_flight": rows("SELECT * FROM marts.fact_flight ORDER BY flight_key LIMIT 3"),
    "fact_delay_reason": rows("SELECT * FROM marts.fact_delay_reason ORDER BY delay_fact_key LIMIT 5"),
}

result = {
    "row_counts": row_counts,
    "status": status,
    "status_by_year": status_by_year,
    "year_counts": year_counts,
    "cancellation": cancellation,
    "delay_reasons": delay_reasons,
    "delay_reasons_by_year": delay_reasons_by_year,
    "reasons_per_flight": reasons_per_flight,
    "delay_totals": delay_totals,
    "flight_metrics": flight_metrics,
    "delayed_minutes": delayed_minutes,
    "top_carriers": top_carriers,
    "top_origins": top_origins,
    "regions": regions,
    "distance_groups": distance_groups,
    "airports_by_region": airports_by_region,
    "airports_by_state": airports_by_state,
    "date_months": date_months,
    "samples": samples,
}
with open(OUT, "w", encoding="utf8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
print(json.dumps({
    "row_counts": row_counts,
    "flight_metrics": flight_metrics,
    "status": status,
    "year_counts": year_counts,
    "delay_totals": delay_totals,
    "delay_reasons": delay_reasons,
    "reasons_per_flight": reasons_per_flight,
    "delayed_minutes": delayed_minutes,
    "regions": regions,
    "distance_groups": distance_groups,
    "airports_by_region": airports_by_region,
}, ensure_ascii=False, indent=2, default=str))
print("saved", OUT)
