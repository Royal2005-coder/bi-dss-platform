# -*- coding: utf-8 -*-
"""Leakage-safe network-state nowcast features on an absolute UTC clock (8.4.2, v12).

Prediction moment: T_pred = scheduled departure (UTC) - H minutes.
An event of ANOTHER flight is usable only if the moment it becomes known <= T_pred:
  * departure event   : known at actual gate departure  = sched_dep_utc + dep_delay
  * arrival event     : known at actual arrival         = sched_dep_utc + crs_elapsed + arr_delay
  * cancellation event: known (conservatively) at its scheduled departure time
The flight being predicted can never leak: its own departure happens after T_pred
(unless it left > H minutes early), its arrival and cancellation even later.

Groups (dense enough in the 10 % BTS sample): national, carrier, origin region, carrier x region.
Windows: 60, 120, 360 and 720 minutes before T_pred.
Local -> UTC conversion uses IANA zones (timezonefinder on airport lat/lon + zoneinfo, DST-aware)
and is cross-checked against the CRS timetable (arrival local time must match).

Usage: python build_network_nowcast.py --horizon 60
"""
import argparse
import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
from timezonefinder import TimezoneFinder

DB = os.environ.get("BI_DUCKDB_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "warehouse.duckdb"))
from flights_source import END, EPOCH, flights_sql  # noqa: E402
WINDOWS = (60, 120, 360, 720)
GROUPS = {"nat": "'ALL'", "car": "carrier_code", "reg": "origin_region",
          "cr": "carrier_code || '_' || origin_region"}


def utc_offsets(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return (airport, flight_date, off_min) with off_min = local - UTC at local noon."""
    ap = con.execute("SELECT airport_code, COALESCE(region, 'UNK') AS region, latitude, longitude FROM seeds.airport_master "
                     "WHERE latitude IS NOT NULL").fetchdf()
    tf = TimezoneFinder()
    ap["tz"] = [tf.timezone_at(lng=lo, lat=la) for la, lo in zip(ap.latitude, ap.longitude)]
    if ap["tz"].isna().any():
        raise ValueError(f"unresolved time zones: {ap.loc[ap.tz.isna(), 'airport_code'].tolist()}")
    days = [EPOCH + timedelta(days=i) for i in range((END - EPOCH).days + 1)]
    rows = []
    for code, tz in zip(ap.airport_code, ap.tz):
        z = ZoneInfo(tz)
        for d in days:
            off = datetime(d.year, d.month, d.day, 12, tzinfo=z).utcoffset()
            rows.append((code, d, int(off.total_seconds() // 60)))
    return pd.DataFrame(rows, columns=["airport", "flight_date", "off_min"]), ap[["airport_code", "region", "tz"]]


def build(out_path: str, horizon: int) -> None:
    t0 = time.time()
    con = duckdb.connect(DB, read_only=True)
    con.execute("SET threads TO 8")
    con.execute(f"SET memory_limit = '{os.environ.get('BI_DUCKDB_MEM', '3GB')}'")
    con.execute("SET preserve_insertion_order = false")
    tmp = os.path.join(os.environ.get("BI_WORK_DIR", os.path.dirname(os.path.abspath(__file__))), "duckdb_tmp")
    con.execute(f"SET temp_directory = '{tmp.replace(os.sep, '/')}'")
    off, ap = utc_offsets(con)
    con.register("off_df", off)
    con.register("ap_df", ap)
    con.execute("CREATE TEMP TABLE off AS SELECT * FROM off_df")
    con.execute(
        f"""
        CREATE TEMP TABLE f AS
        SELECT
            s.flight_key,
            s.flight_date, s.carrier_code, a.region AS origin_region,
            (s.crs_dep_time // 100) * 60 + s.crs_dep_time % 100 AS dep_m,
            (s.crs_arr_time // 100) * 60 + s.crs_arr_time % 100 AS arr_m,
            s.crs_elapsed_time AS el, s.dep_delay, s.arr_delay, s.cancelled, s.diverted,
            date_diff('day', DATE '{EPOCH}', s.flight_date) * 1440
              + (s.crs_dep_time // 100) * 60 + s.crs_dep_time % 100 - oo.off_min AS sdep_utc,
            od.off_min AS dest_off
        FROM ({flights_sql()}) s
        JOIN ap_df a ON a.airport_code = s.origin_code
        JOIN off oo ON oo.airport = s.origin_code AND oo.flight_date = s.flight_date
        LEFT JOIN off od ON od.airport = s.dest_code AND od.flight_date = s.flight_date
        """
    )
    # cross-check: UTC schedule + CRS elapsed, shown in destination local clock, must equal CRS_ARR
    chk = con.execute(
        """
        SELECT AVG(CASE WHEN ((CAST(sdep_utc + el + dest_off AS BIGINT)) % 1440 + 1440) % 1440
                            = arr_m % 1440 THEN 1.0 ELSE 0.0 END)
        FROM f WHERE el IS NOT NULL AND arr_m IS NOT NULL AND dest_off IS NOT NULL
        """
    ).fetchone()[0]
    print(f"tz cross-check vs CRS_ARR: {chk:.4f} exact match", flush=True)
    con.execute(f"ALTER TABLE f ADD COLUMN t_pred BIGINT; UPDATE f SET t_pred = sdep_utc - {int(horizon)}")

    # event streams aggregated to unique (group, known_time) then cumulated
    events = {
        "dep": ("sdep_utc + dep_delay", "dep_delay", "cancelled = 0 AND dep_delay IS NOT NULL"),
        "arr": ("sdep_utc + el + arr_delay", "arr_delay",
                "cancelled = 0 AND diverted = 0 AND arr_delay IS NOT NULL AND el IS NOT NULL"),
    }
    for g, gexpr in GROUPS.items():
        for ev, (kt, val, where) in events.items():
            con.execute(
                f"""
                CREATE TEMP TABLE s_{g}_{ev} AS
                SELECT g, kt,
                       SUM(n)   OVER w AS c_n,
                       SUM(sv)  OVER w AS c_sum,
                       SUM(s15) OVER w AS c_15
                FROM (SELECT {gexpr} AS g, CAST({kt} AS BIGINT) AS kt, COUNT(*) AS n,
                             SUM({val}) AS sv, SUM(CASE WHEN {val} >= 15 THEN 1 ELSE 0 END) AS s15
                      FROM f WHERE {where} GROUP BY 1, 2) x
                WINDOW w AS (PARTITION BY g ORDER BY kt ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                """
            )
        con.execute(
            f"""
            CREATE TEMP TABLE s_{g}_sch AS
            SELECT g, kt, SUM(n) OVER w AS c_n, SUM(nc) OVER w AS c_c
            FROM (SELECT {gexpr} AS g, sdep_utc AS kt, COUNT(*) AS n, SUM(cancelled) AS nc
                  FROM f GROUP BY 1, 2) x
            WINDOW w AS (PARTITION BY g ORDER BY kt ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
            """
        )

    def asof(stream: str, gexpr: str, lag: int, cols: list[str]) -> str:
        sel = ", ".join(f"COALESCE(s.{c}, 0) AS {c}" for c in cols)
        return f"""
            SELECT q.flight_key, {sel}
            FROM (SELECT flight_key, {gexpr} AS g, t_pred - {lag} AS tq FROM f) q
            ASOF LEFT JOIN {stream} s ON s.g = q.g AND s.kt <= q.tq
        """

    # one group at a time -> per-group parquet (bounded memory), temp tables dropped after each group
    part_paths = []
    for g, gexpr in GROUPS.items():
        select_parts, joins, temps = [], [], []
        for ev in ("dep", "arr"):
            cols = ["c_n", "c_sum", "c_15"]
            con.execute(f"CREATE TEMP TABLE q_{g}_{ev}_0 AS {asof(f's_{g}_{ev}', gexpr, 0, cols)}")
            temps.append(f"q_{g}_{ev}_0")
            for w in WINDOWS:
                con.execute(f"CREATE TEMP TABLE q_{g}_{ev}_{w} AS {asof(f's_{g}_{ev}', gexpr, w, cols)}")
                a, b = f"q_{g}_{ev}_0", f"q_{g}_{ev}_{w}"
                temps.append(b)
                n = f"({a}.c_n - {b}.c_n)"
                select_parts += [
                    f"CAST({n} AS FLOAT) AS {g}_{ev}_n_{w}",
                    f"CAST(({a}.c_sum - {b}.c_sum) / NULLIF({n}, 0) AS FLOAT) AS {g}_{ev}_mean_{w}",
                    f"CAST(({a}.c_15 - {b}.c_15) / NULLIF({n}, 0) AS FLOAT) AS {g}_{ev}_r15_{w}",
                ]
                joins.append(f"JOIN {b} ON {b}.flight_key = q_{g}_dep_0.flight_key")
        cols = ["c_n", "c_c"]
        con.execute(f"CREATE TEMP TABLE q_{g}_sch_0 AS {asof(f's_{g}_sch', gexpr, 0, cols)}")
        con.execute(f"CREATE TEMP TABLE q_{g}_sch_360 AS {asof(f's_{g}_sch', gexpr, 360, cols)}")
        temps += [f"q_{g}_sch_0", f"q_{g}_sch_360"]
        a, b = f"q_{g}_sch_0", f"q_{g}_sch_360"
        select_parts.append(
            f"CAST(({a}.c_c - {b}.c_c) / NULLIF({a}.c_n - {b}.c_n, 0) AS FLOAT) AS {g}_cancel_rate_360")
        base = f"q_{g}_dep_0"
        joins = [f"JOIN q_{g}_arr_0 ON q_{g}_arr_0.flight_key = {base}.flight_key"] + joins + [
            f"JOIN {a} ON {a}.flight_key = {base}.flight_key", f"JOIN {b} ON {b}.flight_key = {base}.flight_key"]
        part = out_path.replace(".parquet", f"_{g}.parquet")
        con.execute(f"""COPY (SELECT {base}.flight_key, {', '.join(select_parts)} FROM {base} {' '.join(joins)}
                         ORDER BY {base}.flight_key) TO '{part}' (FORMAT PARQUET)""")
        for t in temps:
            con.execute(f"DROP TABLE {t}")
        part_paths.append(part)
        print(f"  group {g}: {len(select_parts)} features", flush=True)
    # every part is complete (one row per flight, ordered by flight_key) -> positional join in DuckDB
    reads = [f"read_parquet('{pp}')" for pp in part_paths]
    sel = ", ".join(["p0.*"] + [f"p{i}.* EXCLUDE (flight_key)" for i in range(1, len(reads))])
    frm = f"{reads[0]} p0 " + " ".join(f"POSITIONAL JOIN {r} p{i}" for i, r in enumerate(reads[1:], 1))
    con.execute(f"COPY (SELECT {sel} FROM {frm}) TO '{out_path}' (FORMAT PARQUET)")
    chk = con.execute(f"SELECT COUNT(*) FROM {frm} WHERE " + " OR ".join(
        f"p{i}.flight_key <> p0.flight_key" for i in range(1, len(reads)))).fetchone()[0]
    assert chk == 0, f"positional join misaligned rows: {chk}"
    for pp in part_paths:
        os.remove(pp)
    n = con.execute(f"SELECT COUNT(*), AVG(nat_dep_n_120), AVG(car_dep_n_120) FROM read_parquet('{out_path}')").fetchone()
    print(f"wrote {out_path} rows={n[0]} mean nat_dep_n_120={n[1]:.1f} car_dep_n_120={n[2]:.1f} "
          f"H={horizon} in {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, default=60)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    out = a.out or os.path.join(os.environ.get("BI_WORK_DIR", os.path.join(os.path.dirname(__file__), "..", "work")), f"network_h{a.horizon}_v2.parquet")
    build(os.path.abspath(out).replace("\\", "/"), a.horizon)
