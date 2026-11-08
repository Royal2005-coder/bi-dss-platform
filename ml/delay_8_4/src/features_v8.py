# -*- coding: utf-8 -*-
"""Step 2 - v8 feature layer in DuckDB (all pre-event, time-safe).

Adds to features.parquet: rolling prior delay rates by 14 groups (previous data-day, last 7 / 30
data-days, smoothed expanding prior; every window ends at 1 PRECEDING, i.e. strictly before the
flight date), daily weather at origin/destination (Open-Meteo archive = perfect-forecast proxy),
hourly congestion counts and hub size (hub size is recomputed on the train window in train_final).
Writes $BI_WORK_DIR/features_v8.parquet.
"""
import os

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(ROOT, "work"))
os.makedirs(WORK, exist_ok=True)
FEAT = os.path.join(WORK, "features.parquet").replace("\\", "/")
WX = os.environ.get("BI_WEATHER_PATH", os.path.join(ROOT, "data", "weather_daily.parquet")).replace("\\", "/")

GROUPS = [
    ("carrier_code",),
    ("origin",),
    ("dest",),
    ("route_key",),
    ("carrier_code", "route_key"),
    ("origin", "scheduled_dep_hour"),
    ("dest", "scheduled_arr_hour"),
    ("carrier_code", "scheduled_dep_hour"),
    ("carrier_code", "origin"),
    ("carrier_code", "dest"),
    ("origin", "month"),
    ("carrier_code", "month"),
    ("origin", "weekday_number"),
    ("carrier_code", "weekday_number"),
]
NAMES = ["carrier", "origin", "dest", "route", "carrier_route", "origin_hour",
         "dest_hour", "carrier_hour", "carrier_origin", "carrier_dest",
         "origin_month", "carrier_month", "origin_weekday", "carrier_weekday"]

WX_COLS = ["precip_sum", "precip_max", "wind_max", "gust_max", "visibility_min",
           "cloud_mean", "temp_mean", "weather_code_max", "bad_hours"]


def build_sql():
    parts = []
    parts.append(f"""
    CREATE OR REPLACE TEMP VIEW base AS
    SELECT * FROM read_parquet('{FEAT}')
    """)
    parts.append(f"""
    CREATE OR REPLACE TEMP VIEW wx AS
    SELECT * FROM read_parquet('{WX}')
    """)
    parts.append("""
    CREATE OR REPLACE TEMP VIEW labeled AS
    SELECT * FROM base WHERE target IS NOT NULL
    """)
    sels = ["b.*"]
    for g, name in zip(GROUPS, NAMES):
        gsql = ", ".join(g)
        parts.append(f"""
        CREATE OR REPLACE TEMP VIEW daily_{name} AS
        SELECT {gsql}, date_key, AVG(target) AS rate, COUNT(*) AS n,
               SUM(target) AS pos
        FROM labeled GROUP BY {gsql}, date_key
        """)
        parts.append(f"""
        CREATE OR REPLACE TEMP VIEW roll_{name} AS
        SELECT {gsql}, date_key,
          LAG(rate) OVER (PARTITION BY {gsql} ORDER BY date_key) AS {name}_prev_day,
          SUM(pos) OVER (PARTITION BY {gsql} ORDER BY date_key
                         ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)
            / NULLIF(SUM(n) OVER (PARTITION BY {gsql} ORDER BY date_key
                                  ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING), 0) AS {name}_7d,
          SUM(pos) OVER (PARTITION BY {gsql} ORDER BY date_key
                         ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING)
            / NULLIF(SUM(n) OVER (PARTITION BY {gsql} ORDER BY date_key
                                  ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING), 0) AS {name}_30d,
          (SUM(pos) OVER (PARTITION BY {gsql} ORDER BY date_key
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) + 0.21*20)
            / (SUM(n) OVER (PARTITION BY {gsql} ORDER BY date_key
                            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) + 20) AS {name}_prior
        FROM daily_{name}
        """)
        sels.append(f"r_{name}.{name}_prev_day, r_{name}.{name}_7d, r_{name}.{name}_30d, r_{name}.{name}_prior")
    joins = "\n".join(
        f"LEFT JOIN roll_{name} r_{name} ON " +
        " AND ".join(f"b.{c} = r_{name}.{c}" for c in g) +
        f" AND b.date_key = r_{name}.date_key"
        for g, name in zip(GROUPS, NAMES))
    # weather origin/dest
    wx_sels = []
    for side, alias in (("origin", "wo"), ("dest", "wd")):
        for c in WX_COLS:
            wx_sels.append(f"{alias}.{c} AS {side}_wx_{c}")
        wx_sels.append(f"({alias}.airport_code IS NULL) AS {side}_wx_missing")
    parts.append(f"""
    CREATE OR REPLACE TEMP VIEW feat AS
    SELECT {", ".join(sels)},
      {", ".join(wx_sels)},
      wo.bad_hours + wd.bad_hours AS wx_bad_hours_both,
      GREATEST(wo.wind_max, wd.wind_max) AS wx_wind_max_both,
      wo.precip_sum + wd.precip_sum AS wx_precip_sum_both,
      (wo.bad_hours > 0 OR wd.bad_hours > 0) AS wx_bad_any,
      oc.dep_in_hour AS origin_dep_in_hour,
      ac.arr_in_hour AS dest_arr_in_hour,
      oh.hub_flights AS origin_hub_flights,
      dh.hub_flights AS dest_hub_flights,
      (b.distance_miles / NULLIF(b.crs_elapsed_time_minutes, 0)) AS pace_mph
    FROM base b
    {joins}
    LEFT JOIN wx wo ON b.origin = wo.airport_code AND b.date_key = wo.date_key
    LEFT JOIN wx wd ON b.dest = wd.airport_code AND b.date_key = wd.date_key
    LEFT JOIN (SELECT origin, date_key, scheduled_dep_hour, COUNT(*) AS dep_in_hour
               FROM base GROUP BY 1,2,3) oc
      ON b.origin = oc.origin AND b.date_key = oc.date_key AND b.scheduled_dep_hour = oc.scheduled_dep_hour
    LEFT JOIN (SELECT dest, date_key, scheduled_arr_hour, COUNT(*) AS arr_in_hour
               FROM base GROUP BY 1,2,3) ac
      ON b.dest = ac.dest AND b.date_key = ac.date_key AND b.scheduled_arr_hour = ac.scheduled_arr_hour
    LEFT JOIN (SELECT origin, COUNT(*) AS hub_flights FROM base GROUP BY 1) oh ON b.origin = oh.origin
    LEFT JOIN (SELECT dest, COUNT(*) AS hub_flights FROM base GROUP BY 1) dh ON b.dest = dh.dest
    """)
    return [p.strip() for p in parts if p.strip()]


FEAT_CACHE = os.path.join(WORK, "features_v8.parquet")


def load_features():
    if os.path.exists(FEAT_CACHE):
        print("loading cached features", FEAT_CACHE, flush=True)
        return pd.read_parquet(FEAT_CACHE)
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=2")
    con.execute("SET temp_directory='" + os.path.join(WORK, 'duckdb_tmp').replace(chr(92), '/') + "'")
    for stmt in build_sql():
        con.execute(stmt)
    df = con.execute("SELECT * FROM feat").fetchdf()
    con.close()
    # fill + downcast (same as v8)
    for c in df.columns:
        if c.startswith(("origin_wx_", "dest_wx_")) and c not in ("origin_wx_missing", "dest_wx_missing"):
            df[c] = df[c].fillna(0 if ("precip" in c or "wind" in c or "gust" in c or "bad" in c or "weather_code" in c)
                                else 10000 if "visibility" in c else 50 if "cloud" in c else 13)
    for c in [x for x in df.columns if x.endswith(("_prev_day", "_7d", "_30d", "_prior"))]:
        df[c] = df[c].fillna(0.21)
    df["wx_bad_any"] = df["wx_bad_any"].fillna(False).astype("float32")
    df["pace_mph"] = df["pace_mph"].fillna(500)
    for c in df.select_dtypes(include=["float64"]).columns:
        df[c] = df[c].astype("float32")
    df.to_parquet(FEAT_CACHE, index=False)
    print("cached", FEAT_CACHE, df.shape, flush=True)
    return df



if __name__ == "__main__":
    # run as a pipeline step: always rebuild (run_pipeline.py decides whether the step is skipped)
    if os.path.exists(FEAT_CACHE):
        os.remove(FEAT_CACHE)
    load_features()
