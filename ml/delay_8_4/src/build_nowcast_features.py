"""Leakage-safe operational features ("lich su tau bay" + nowcast tai san bay) cho 8.4.2.

Moc du bao: T_pred = gio cat canh ke hoach (CRS_DEP) - H phut (mac dinh H = 60).
Moi su kien chi duoc dung khi thoi diem no TRO NEN BIET (known_time) <= T_pred:
  - chang bay vao (inbound) cung hang + cung so hieu, ha canh tai san bay di cua chuyen
    can du bao (proxy tau bay vi du lieu BTS khong co TAIL_NUM): biet dep_delay khi no
    thuc su cat canh; neu chua cat canh tai T_pred thi chi biet "da tre it nhat X phut".
  - san bay di / san bay den: so chuyen da cat canh, tre trung binh, ty le tre >= 15,
    backlog (den gio ma chua bay), so chuyen huy - trong cua so 60/180 phut truoc T_pred.
Thoi gian dia phuong khac mui gio duoc quy doi bang lech mui gio suy ra tu chinh lich CRS:
  tz(dest) - tz(origin) = CRS_ARR - CRS_DEP - CRS_ELAPSED (mod 1440).
Khong dung ARR_DELAY / DEP_DELAY cua chinh chuyen can du bao.
"""
import argparse
import os
import time

import duckdb

DB = os.environ.get("BI_DUCKDB_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "warehouse.duckdb"))


def build(out_path: str, horizon: int) -> None:
    t0 = time.time()
    con = duckdb.connect(DB, read_only=True)
    con.execute("SET threads TO 8")
    H = int(horizon)
    con.execute(
        """
        CREATE TEMP TABLE f AS
        SELECT
            CAST(ROW_NUMBER() OVER (ORDER BY flight_date, carrier_code, flight_number,
                                     origin_code, dest_code) AS BIGINT) AS flight_key,
            flight_date, carrier_code, flight_number, origin_code AS origin, dest_code AS dest,
            (crs_dep_time // 100) * 60 + crs_dep_time % 100 AS dep_m,
            (crs_arr_time // 100) * 60 + crs_arr_time % 100 AS arr_m,
            crs_elapsed_time AS el,
            dep_delay, arr_delay, cancelled, diverted
        FROM intermediate.int_flights_2022_2023
        """
    )
    # tz(dest) - tz(origin) theo lich CRS, dua ve [-720, 720)
    con.execute(
        """
        CREATE TEMP TABLE f2 AS
        SELECT *,
            CASE WHEN el IS NULL OR arr_m IS NULL THEN NULL
                 ELSE ((CAST(arr_m - dep_m - el AS INTEGER) % 1440 + 1440 + 720) % 1440) - 720
            END AS tzdiff,
            dep_m - {H} AS t_pred
        FROM f
        """.format(H=H)
    )
    # ha canh sang ngay hom sau (theo dong ho san bay den): moc biet = arr_m + 1440
    con.execute(
        """
        CREATE TEMP TABLE f3 AS
        SELECT *, CASE WHEN tzdiff IS NOT NULL AND dep_m + el + tzdiff >= 1440 THEN 1 ELSE 0 END AS arr_next_day
        FROM f2
        """
    )
    con.execute("DROP TABLE f2")
    con.execute("ALTER TABLE f3 RENAME TO f2"
    )
    # ---------- 1) chang bay vao cung so hieu (proxy tau bay) ----------
    con.execute(
        """
        CREATE TEMP TABLE inbound AS
        SELECT t.flight_key,
               i.arr_m                 AS in_arr_m,
               i.arr_m - i.el          AS in_dep_m_local,          -- gio cat canh KH, quy ve dong ho san bay di
               i.dep_delay             AS in_dep_delay,
               i.cancelled             AS in_cancelled,
               t.t_pred, t.dep_m,
               ROW_NUMBER() OVER (PARTITION BY t.flight_key ORDER BY i.arr_m DESC) AS rn
        FROM f2 t
        JOIN f2 i
          ON i.flight_date = t.flight_date AND i.carrier_code = t.carrier_code
         AND i.flight_number = t.flight_number AND i.dest = t.origin
         AND i.arr_m <= t.dep_m AND i.el IS NOT NULL AND i.arr_next_day = 0
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE inbound_feat AS
        SELECT flight_key,
            1.0 AS has_inbound,
            dep_m - in_arr_m AS sched_turn_min,                        -- quay dau ke hoach (thuan CRS)
            CASE WHEN in_cancelled = 0 AND in_dep_delay IS NOT NULL
                      AND in_dep_m_local + in_dep_delay <= t_pred THEN 1.0
                 WHEN in_cancelled = 1 AND in_dep_m_local <= t_pred THEN 1.0 ELSE 0.0 END AS in_status_known,
            CASE WHEN in_cancelled = 0 AND in_dep_delay IS NOT NULL
                      AND in_dep_m_local + in_dep_delay <= t_pred THEN in_dep_delay END AS in_dep_delay_known,
            CASE WHEN in_cancelled = 1 AND in_dep_m_local <= t_pred THEN 1.0 ELSE 0.0 END AS in_cancel_known,
            -- can duoi do tre da biet tai T_pred
            CASE WHEN in_cancelled = 0 AND in_dep_delay IS NOT NULL
                      AND in_dep_m_local + in_dep_delay <= t_pred THEN in_dep_delay
                 ELSE GREATEST(0, t_pred - in_dep_m_local) END AS in_delay_lb,
            (dep_m - in_arr_m) - (CASE WHEN in_cancelled = 0 AND in_dep_delay IS NOT NULL
                      AND in_dep_m_local + in_dep_delay <= t_pred THEN in_dep_delay
                 ELSE GREATEST(0, t_pred - in_dep_m_local) END) AS turn_slack_lb
        FROM inbound WHERE rn = 1
        """
    )
    # ---------- 2) nowcast san bay bang cumulative stream + ASOF ----------
    def airport_stream(name, airport_col, clock_expr, extra_where):
        con.execute(
            f"""
            CREATE TEMP TABLE {name} AS
            SELECT {airport_col} AS ap, flight_date, kt,
                   SUM(1) OVER w AS c_n, SUM(dly) OVER w AS c_sum, SUM(d15) OVER w AS c_15
            FROM (SELECT {airport_col}, flight_date, {clock_expr} AS kt,
                         {{dly}} AS dly, {{d15}} AS d15 FROM f2 WHERE {extra_where}) s
            WINDOW w AS (PARTITION BY ap, flight_date ORDER BY kt, dly
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
            """.replace("{dly}", "dep_delay").replace("{d15}", "CASE WHEN dep_delay >= 15 THEN 1 ELSE 0 END")
        )

    # cat canh thuc te tai san bay (dong ho dia phuong cua san bay do)
    airport_stream("dep_stream", "origin", "dep_m + dep_delay", "cancelled = 0 AND dep_delay IS NOT NULL")
    # lich cat canh ke hoach (backlog) va huy (biet tai gio ke hoach)
    con.execute(
        """
        CREATE TEMP TABLE sched_stream AS
        SELECT origin AS ap, flight_date, dep_m AS kt,
               COUNT(*) OVER w AS c_sched,
               SUM(cancelled) OVER w AS c_cancel
        FROM f2
        WINDOW w AS (PARTITION BY origin, flight_date ORDER BY dep_m, flight_key
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        """
    )
    # ha canh thuc te vao san bay (dong ho san bay den = san bay do)
    con.execute(
        """
        CREATE TEMP TABLE arr_stream AS
        SELECT dest AS ap, flight_date, kt,
               SUM(1) OVER w AS c_n, SUM(arr_delay) OVER w AS c_sum,  -- kt da cong 1440 neu ha canh hom sau
               SUM(CASE WHEN arr_delay >= 15 THEN 1 ELSE 0 END) OVER w AS c_15
        FROM (SELECT dest, flight_date, arr_m + 1440 * arr_next_day + arr_delay AS kt, arr_delay
              FROM f2 WHERE cancelled = 0 AND diverted = 0 AND arr_delay IS NOT NULL AND arr_m IS NOT NULL) s
        WINDOW w AS (PARTITION BY ap, flight_date ORDER BY kt, arr_delay
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        """
    )

    # truy van ASOF: lay tong tich luy tai moc t (dong ho cua san bay ap)
    def asof(stream, ap_col, t_expr, cols, alias):
        sel = ", ".join(f"COALESCE(s.{c}, 0) AS {alias}_{c}" for c in cols)
        return f"""
            SELECT q.flight_key, {sel}
            FROM (SELECT flight_key, {ap_col} AS ap, flight_date, {t_expr} AS tq FROM f2
                  WHERE {t_expr} IS NOT NULL) q
            ASOF LEFT JOIN {stream} s
              ON s.ap = q.ap AND s.flight_date = q.flight_date AND s.kt <= q.tq
        """

    parts = {
        # san bay di, dong ho san bay di
        "o_dep_0": asof("dep_stream", "origin", "t_pred", ["c_n", "c_sum", "c_15"], "od0"),
        "o_dep_60": asof("dep_stream", "origin", "t_pred - 60", ["c_n", "c_sum", "c_15"], "od60"),
        "o_dep_180": asof("dep_stream", "origin", "t_pred - 180", ["c_n", "c_sum", "c_15"], "od180"),
        "o_sch_0": asof("sched_stream", "origin", "t_pred", ["c_sched", "c_cancel"], "os0"),
        "o_sch_180": asof("sched_stream", "origin", "t_pred - 180", ["c_sched", "c_cancel"], "os180"),
        "o_arr_0": asof("arr_stream", "origin", "t_pred", ["c_n", "c_sum", "c_15"], "oa0"),
        "o_arr_60": asof("arr_stream", "origin", "t_pred - 60", ["c_n", "c_sum", "c_15"], "oa60"),
        # san bay den, quy doi T_pred sang dong ho san bay den
        "d_dep_0": asof("dep_stream", "dest", "t_pred + tzdiff", ["c_n", "c_sum", "c_15"], "dd0"),
        "d_dep_60": asof("dep_stream", "dest", "t_pred + tzdiff - 60", ["c_n", "c_sum", "c_15"], "dd60"),
        "d_arr_0": asof("arr_stream", "dest", "t_pred + tzdiff", ["c_n", "c_sum", "c_15"], "da0"),
        "d_arr_60": asof("arr_stream", "dest", "t_pred + tzdiff - 60", ["c_n", "c_sum", "c_15"], "da60"),
    }
    for name, sql in parts.items():
        con.execute(f"CREATE TEMP TABLE {name} AS {sql}")

    con.execute(
        f"""
        COPY (
        SELECT b.flight_key,
            COALESCE(i.has_inbound, 0.0)       AS has_inbound,
            i.sched_turn_min, i.in_status_known, i.in_dep_delay_known, i.in_cancel_known,
            i.in_delay_lb, i.turn_slack_lb,
            -- san bay di: 60 phut gan nhat truoc T_pred
            (a.od0_c_n - a60.od60_c_n)                                   AS o_dep_n_60,
            (a.od0_c_sum - a60.od60_c_sum) / NULLIF(a.od0_c_n - a60.od60_c_n, 0) AS o_dep_mean_60,
            (a.od0_c_15 - a60.od60_c_15) / NULLIF(a.od0_c_n - a60.od60_c_n, 0)   AS o_dep_r15_60,
            (a.od0_c_sum - a180.od180_c_sum) / NULLIF(a.od0_c_n - a180.od180_c_n, 0) AS o_dep_mean_180,
            (a.od0_c_15 - a180.od180_c_15) / NULLIF(a.od0_c_n - a180.od180_c_n, 0)   AS o_dep_r15_180,
            a.od0_c_sum / NULLIF(a.od0_c_n, 0)                           AS o_dep_mean_day,
            a.od0_c_15 / NULLIF(a.od0_c_n, 0)                            AS o_dep_r15_day,
            (s.os0_c_sched - s.os0_c_cancel - a.od0_c_n)                 AS o_backlog,
            (s.os0_c_cancel - s180.os180_c_cancel)                       AS o_cancel_180,
            s.os0_c_cancel                                               AS o_cancel_day,
            (r.oa0_c_sum - r60.oa60_c_sum) / NULLIF(r.oa0_c_n - r60.oa60_c_n, 0) AS o_arr_mean_60,
            r.oa0_c_15 / NULLIF(r.oa0_c_n, 0)                            AS o_arr_r15_day,
            -- san bay den
            (dd.dd0_c_sum - dd60.dd60_c_sum) / NULLIF(dd.dd0_c_n - dd60.dd60_c_n, 0) AS d_dep_mean_60,
            dd.dd0_c_15 / NULLIF(dd.dd0_c_n, 0)                          AS d_dep_r15_day,
            (da.da0_c_sum - da60.da60_c_sum) / NULLIF(da.da0_c_n - da60.da60_c_n, 0) AS d_arr_mean_60,
            (da.da0_c_15 - da60.da60_c_15) / NULLIF(da.da0_c_n - da60.da60_c_n, 0)   AS d_arr_r15_60,
            da.da0_c_15 / NULLIF(da.da0_c_n, 0)                          AS d_arr_r15_day
        FROM f2 b
        LEFT JOIN inbound_feat i USING (flight_key)
        LEFT JOIN o_dep_0 a USING (flight_key)
        LEFT JOIN o_dep_60 a60 USING (flight_key)
        LEFT JOIN o_dep_180 a180 USING (flight_key)
        LEFT JOIN o_sch_0 s USING (flight_key)
        LEFT JOIN o_sch_180 s180 USING (flight_key)
        LEFT JOIN o_arr_0 r USING (flight_key)
        LEFT JOIN o_arr_60 r60 USING (flight_key)
        LEFT JOIN d_dep_0 dd USING (flight_key)
        LEFT JOIN d_dep_60 dd60 USING (flight_key)
        LEFT JOIN d_arr_0 da USING (flight_key)
        LEFT JOIN d_arr_60 da60 USING (flight_key)
        ORDER BY b.flight_key
        ) TO '{out_path}' (FORMAT PARQUET)
        """
    )
    n = con.execute(f"SELECT COUNT(*), AVG(has_inbound), AVG(in_status_known) FROM read_parquet('{out_path}')").fetchone()
    print(f"wrote {out_path} rows={n[0]} has_inbound={n[1]:.3f} inbound_known={n[2]:.3f} "
          f"H={H} in {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(os.environ.get("BI_WORK_DIR", os.path.join(os.path.dirname(__file__), "..", "work")), f"nowcast_h{a.horizon}.parquet")
    build(os.path.abspath(out).replace("\\", "/"), a.horizon)
