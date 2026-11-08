# -*- coding: utf-8 -*-
"""v15 - hourly weather nowcast (Meteostat, UTC) known at T_pred = CRS_DEP - H; replaces daily weather.

Only observations stamped <= T_pred - LAG_MIN are used (METARs are published after the hour), so no
future weather enters the model, unlike the v14 daily aggregate (perfect-forecast proxy).
Per side (origin o_, destination d_): latest obs (temp, dew-point depression, humidity, wind, gust,
precip, snow depth, severity) + 3h/6h windows (precip sum, max gust/wind, max severity, thunder, snow/ice,
fog hours, 3h pressure change) + obs age. Network: number of the 30 busiest airports with thunderstorm /
severe weather in the last 3h. Writes $BI_WORK_DIR/weather_h{H}.parquet.
"""
from __future__ import annotations

import argparse
import os
import time

import duckdb
import pandas as pd

from build_network_nowcast import utc_offsets
from flights_source import EPOCH, flights_sql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("BI_DUCKDB_PATH", os.path.join(ROOT, "data", "warehouse.duckdb"))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(ROOT, "work"))
WX = os.environ.get("BI_WEATHER_HOURLY", os.path.join(ROOT, "data", "weather_hourly_utc.parquet"))
LAG_MIN = 60
N_HUBS = 30
# Meteostat condition code -> severity 0..5
SEV = {1: 0, 2: 0, 3: 0, 4: 0, 5: 2, 6: 3, 7: 1, 8: 2, 9: 3, 10: 4, 11: 4, 12: 3, 13: 3, 14: 2, 15: 3, 16: 4,
       17: 2, 18: 3, 19: 3, 20: 3, 21: 3, 22: 4, 23: 3, 24: 4, 25: 4, 26: 5, 27: 5}
THUNDER, SNOWICE, FOG = (23, 24, 25, 26, 27), (10, 11, 12, 13, 14, 15, 16, 19, 20, 21, 22), (5, 6)


def build(out_path: str, horizon: int) -> None:
    t0 = time.time()
    con = duckdb.connect(DB, read_only=True)
    con.execute("SET threads TO 8")
    con.execute(f"SET memory_limit = '{os.environ.get('BI_DUCKDB_MEM', '3GB')}'")
    con.execute(f"SET temp_directory = '{os.path.join(WORK, 'duckdb_tmp').replace(os.sep, '/')}'")
    off, _ = utc_offsets(con)
    con.register("off_df", off)
    con.register("sev_df", pd.DataFrame({"coco": list(SEV), "sev": list(SEV.values())}))
    wx = WX.replace(os.sep, "/")
    lst = lambda codes: ", ".join(map(str, codes))  # noqa: E731
    con.execute(f"""
        CREATE TEMP TABLE h AS
        SELECT w.airport AS ap, w.ts_utc, w.temp, w.temp - w.dwpt AS dpd, w.rhum, COALESCE(w.prcp, 0) AS prcp,
               w.snow, w.wspd, w.wpgt, w.pres, COALESCE(s.sev, 0) AS sev,
               CAST(w.coco IN ({lst(THUNDER)}) AS INTEGER) AS thunder,
               CAST(w.coco IN ({lst(SNOWICE)}) AS INTEGER) AS snowice,
               CAST(w.coco IN ({lst(FOG)}) AS INTEGER) AS fog
        FROM read_parquet('{wx}') w LEFT JOIN sev_df s ON s.coco = CAST(w.coco AS INTEGER)
    """)
    con.execute("""
        CREATE TEMP TABLE hr AS
        SELECT ap, ts_utc, temp, dpd, rhum, prcp, snow, wspd, wpgt, sev,
               SUM(prcp) OVER w3 AS prcp_3h, SUM(prcp) OVER w6 AS prcp_6h,
               MAX(COALESCE(wpgt, wspd)) OVER w3 AS gust_max_3h, MAX(wspd) OVER w3 AS wspd_max_3h,
               MAX(sev) OVER w3 AS sev_max_3h, MAX(sev) OVER w6 AS sev_max_6h,
               SUM(thunder) OVER w6 AS thunder_6h, SUM(snowice) OVER w6 AS snowice_6h, SUM(fog) OVER w3 AS fog_3h,
               pres - FIRST_VALUE(pres) OVER w3 AS pres_chg_3h,
               MAX(thunder) OVER w3 AS thunder_3h, CAST(MAX(sev) OVER w3 >= 3 AS INTEGER) AS severe_3h
        FROM h
        WINDOW w3 AS (PARTITION BY ap ORDER BY ts_utc RANGE BETWEEN INTERVAL 2 HOURS PRECEDING AND CURRENT ROW),
               w6 AS (PARTITION BY ap ORDER BY ts_utc RANGE BETWEEN INTERVAL 5 HOURS PRECEDING AND CURRENT ROW)
    """)
    # network weather: busiest airports by flights before 2023-05-01 (no look-ahead into validation/test)
    con.execute(f"""
        CREATE TEMP TABLE f AS
        SELECT s.flight_key, s.origin_code AS origin, s.dest_code AS dest,
               TIMESTAMP '{EPOCH} 00:00:00' + INTERVAL (date_diff('day', DATE '{EPOCH}', s.flight_date) * 1440
                   + (s.crs_dep_time // 100) * 60 + s.crs_dep_time % 100 - o.off_min - {int(horizon)} - {LAG_MIN}) MINUTE AS tq
        FROM ({flights_sql()}) s
        JOIN off_df o ON o.airport = s.origin_code AND o.flight_date = s.flight_date
    """)
    con.execute(f"""
        CREATE TEMP TABLE hubs AS SELECT origin AS ap FROM f WHERE tq < TIMESTAMP '2023-05-01' GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT {N_HUBS}
    """)
    con.execute("""
        CREATE TEMP TABLE netwx AS
        SELECT ts_utc, SUM(thunder_3h) AS hubs_thunder_3h, SUM(severe_3h) AS hubs_severe_3h
        FROM hr WHERE ap IN (SELECT ap FROM hubs) GROUP BY 1
    """)
    cols = ["temp", "dpd", "rhum", "prcp", "snow", "wspd", "wpgt", "sev", "prcp_3h", "prcp_6h", "gust_max_3h",
            "wspd_max_3h", "sev_max_3h", "sev_max_6h", "thunder_6h", "snowice_6h", "fog_3h", "pres_chg_3h"]
    for side, col in (("o", "origin"), ("d", "dest")):
        sel = ", ".join(f"CAST(r.{c} AS FLOAT) AS {side}_wxh_{c}" for c in cols)
        con.execute(f"""
            CREATE TEMP TABLE w_{side} AS
            SELECT q.flight_key, {sel},
                   CAST(date_diff('minute', r.ts_utc, q.tq) / 60.0 AS FLOAT) AS {side}_wxh_age_h
            FROM (SELECT flight_key, {col} AS ap, tq FROM f) q
            ASOF LEFT JOIN hr r ON r.ap = q.ap AND r.ts_utc <= q.tq
        """)
    con.execute("""
        CREATE TEMP TABLE w_n AS
        SELECT q.flight_key, CAST(n.hubs_thunder_3h AS FLOAT) AS nat_wxh_hubs_thunder_3h,
               CAST(n.hubs_severe_3h AS FLOAT) AS nat_wxh_hubs_severe_3h
        FROM (SELECT flight_key, tq FROM f) q ASOF LEFT JOIN netwx n ON n.ts_utc <= q.tq
    """)
    con.execute(f"""
        COPY (SELECT o.*, d.* EXCLUDE (flight_key), n.* EXCLUDE (flight_key),
                     CAST(GREATEST(o.o_wxh_sev_max_3h, d.d_wxh_sev_max_3h) AS FLOAT) AS wxh_sev_max_3h_both
              FROM w_o o JOIN w_d d USING (flight_key) JOIN w_n n USING (flight_key) ORDER BY o.flight_key)
        TO '{out_path}' (FORMAT PARQUET)
    """)
    r = con.execute(f"""SELECT COUNT(*), AVG(CASE WHEN o_wxh_age_h <= 3 THEN 1.0 ELSE 0 END),
                               AVG(o_wxh_age_h), AVG(nat_wxh_hubs_thunder_3h) FROM read_parquet('{out_path}')""").fetchone()
    print(f"wrote {out_path} rows={r[0]} origin_obs_within_3h={r[1]:.3f} mean_age_h={r[2]:.2f} "
          f"mean_hubs_thunder={r[3]:.2f} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, default=60)
    a = p.parse_args()
    os.makedirs(WORK, exist_ok=True)
    build(os.path.join(WORK, f"weather_h{a.horizon}.parquet").replace(os.sep, "/"), a.horizon)
