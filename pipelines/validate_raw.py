#!/usr/bin/env python3
import json
import os

import duckdb

BASE = os.environ.get("BI_BASE", "/workspace")
CSV = os.path.join(BASE, "data", "flights_sample_3m.csv")
OUT = os.path.join(BASE, "out", "raw_profile.json")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

con = duckdb.connect()
con.execute("SET memory_limit='3GB'")
con.execute("SET threads=8")


def rows(sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(sql):
    return rows(sql)[0]


con.execute(
    f"CREATE OR REPLACE VIEW raw AS SELECT * FROM read_csv_auto('{CSV}', header=true, sample_size=200000)"
)
desc = con.execute("DESCRIBE raw").fetchall()
types = {r[0]: r[1] for r in desc}
cols = [r[0] for r in desc]

total = one(
    "SELECT COUNT(*) AS n, MIN(CAST(FL_DATE AS DATE)) AS date_min, MAX(CAST(FL_DATE AS DATE)) AS date_max FROM raw"
)
by_year_raw = rows(
    """
    SELECT YEAR(CAST(FL_DATE AS DATE)) AS year, COUNT(*) AS flights,
           SUM(CASE WHEN CANCELLED = 1 THEN 1 ELSE 0 END) AS cancelled,
           SUM(CASE WHEN DIVERTED = 1 THEN 1 ELSE 0 END) AS diverted,
           SUM(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 AND ARR_DELAY >= 15 THEN 1 ELSE 0 END) AS delayed15,
           ROUND(AVG(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 THEN ARR_DELAY END), 2) AS avg_arr_delay
    FROM raw GROUP BY 1 ORDER BY 1
    """
)

con.execute(
    "CREATE OR REPLACE VIEW f AS SELECT * FROM raw WHERE CAST(FL_DATE AS DATE) BETWEEN DATE '2022-01-01' AND DATE '2023-12-31'"
)

filtered = one(
    "SELECT COUNT(*) AS n, MIN(CAST(FL_DATE AS DATE)) AS date_min, MAX(CAST(FL_DATE AS DATE)) AS date_max FROM f"
)
status = one(
    """
    SELECT COUNT(*) AS n,
           SUM(CASE WHEN CANCELLED = 1 THEN 1 ELSE 0 END) AS cancelled,
           SUM(CASE WHEN DIVERTED = 1 THEN 1 ELSE 0 END) AS diverted,
           SUM(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 AND ARR_DELAY >= 15 THEN 1 ELSE 0 END) AS delayed15,
           SUM(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 AND (ARR_DELAY < 15 OR ARR_DELAY IS NULL) THEN 1 ELSE 0 END) AS ontime_or_unknown,
           SUM(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 AND ARR_DELAY IS NULL THEN 1 ELSE 0 END) AS completed_missing_arr,
           SUM(CASE WHEN CANCELLED = 1 AND DIVERTED = 1 THEN 1 ELSE 0 END) AS both_cancelled_diverted
    FROM f
    """
)
status_by_year = rows(
    """
    SELECT YEAR(CAST(FL_DATE AS DATE)) AS year, COUNT(*) AS flights,
           SUM(CASE WHEN CANCELLED = 1 THEN 1 ELSE 0 END) AS cancelled,
           SUM(CASE WHEN DIVERTED = 1 THEN 1 ELSE 0 END) AS diverted,
           SUM(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 AND ARR_DELAY >= 15 THEN 1 ELSE 0 END) AS delayed15,
           ROUND(AVG(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 THEN ARR_DELAY END), 2) AS avg_arr_delay
    FROM f GROUP BY 1 ORDER BY 1
    """
)

missing = {}
empty = {}
for c in cols:
    missing[c] = int(con.execute(f"SELECT COUNT(*) - COUNT({c}) FROM f").fetchone()[0])
    if types[c] == "VARCHAR":
        n = con.execute(
            f"SELECT COUNT(*) FROM f WHERE {c} IS NOT NULL AND TRIM({c}) = ''"
        ).fetchone()[0]
        if n:
            empty[c] = int(n)

distinct = one(
    """
    SELECT COUNT(DISTINCT AIRLINE_CODE) AS carriers,
           COUNT(DISTINCT AIRLINE) AS carrier_names,
           COUNT(DISTINCT ORIGIN) AS origins,
           COUNT(DISTINCT DEST) AS dests,
           COUNT(DISTINCT ORIGIN || '|' || DEST) AS routes
    FROM f
    """
)
airports = one(
    "SELECT COUNT(*) AS airports FROM (SELECT ORIGIN AS code FROM f UNION SELECT DEST FROM f)"
)
duplicates = one(
    """
    SELECT COUNT(*) AS dup_groups, COALESCE(SUM(c - 1), 0) AS dup_rows
    FROM (
        SELECT FL_DATE, AIRLINE, FL_NUMBER, ORIGIN, DEST, COUNT(*) AS c
        FROM f GROUP BY ALL HAVING COUNT(*) > 1
    )
    """
)
duplicates_with_time = one(
    """
    SELECT COUNT(*) AS dup_groups, COALESCE(SUM(c - 1), 0) AS dup_rows
    FROM (
        SELECT FL_DATE, AIRLINE, FL_NUMBER, ORIGIN, DEST, CRS_DEP_TIME, COUNT(*) AS c
        FROM f GROUP BY ALL HAVING COUNT(*) > 1
    )
    """
)

reason_cols = [
    "DELAY_DUE_CARRIER",
    "DELAY_DUE_WEATHER",
    "DELAY_DUE_NAS",
    "DELAY_DUE_SECURITY",
    "DELAY_DUE_LATE_AIRCRAFT",
]
reason_select = []
for c in reason_cols:
    a = c.lower()
    reason_select += [
        f"SUM(CASE WHEN COALESCE({c},0) > 0 THEN 1 ELSE 0 END) AS {a}_rows",
        f"SUM(COALESCE({c},0)) AS {a}_minutes",
        f"MAX(COALESCE({c},0)) AS {a}_max",
    ]
null_bits = " + ".join(f"CASE WHEN {c} IS NULL THEN 1 ELSE 0 END" for c in reason_cols)
reason_select += [
    f"SUM(CASE WHEN {null_bits} = 5 THEN 1 ELSE 0 END) AS all_null",
    f"SUM(CASE WHEN {null_bits} BETWEEN 1 AND 4 THEN 1 ELSE 0 END) AS partial_null",
    "SUM(CASE WHEN COALESCE(DELAY_DUE_CARRIER,0) < 0 OR COALESCE(DELAY_DUE_WEATHER,0) < 0 OR COALESCE(DELAY_DUE_NAS,0) < 0 OR COALESCE(DELAY_DUE_SECURITY,0) < 0 OR COALESCE(DELAY_DUE_LATE_AIRCRAFT,0) < 0 THEN 1 ELSE 0 END) AS negative_rows",
]
reason_stats = one("SELECT " + ", ".join(reason_select) + " FROM f")
reason_total = {
    "rows": sum(reason_stats[f"{c.lower()}_rows"] for c in reason_cols),
    "minutes": sum(reason_stats[f"{c.lower()}_minutes"] for c in reason_cols),
    "flights_with_reason": int(
        con.execute(
            """
            SELECT COUNT(*) FROM f
            WHERE GREATEST(COALESCE(DELAY_DUE_CARRIER,0), COALESCE(DELAY_DUE_WEATHER,0),
                           COALESCE(DELAY_DUE_NAS,0), COALESCE(DELAY_DUE_SECURITY,0),
                           COALESCE(DELAY_DUE_LATE_AIRCRAFT,0)) > 0
            """
        ).fetchone()[0]
    ),
}

reason_sum_expr = " + ".join(f"COALESCE({c},0)" for c in reason_cols)
consistency = one(
    f"""
    WITH x AS (
        SELECT ARR_DELAY, CANCELLED, DIVERTED, ({reason_sum_expr}) AS reason_sum
        FROM f
    )
    SELECT
        COUNT(*) FILTER (WHERE ARR_DELAY IS NOT NULL) AS flights_with_arr,
        COUNT(*) FILTER (WHERE ARR_DELAY IS NOT NULL AND ABS(ARR_DELAY - reason_sum) <= 1) AS sum_match,
        COUNT(*) FILTER (WHERE ARR_DELAY IS NOT NULL AND ABS(ARR_DELAY - reason_sum) > 1) AS sum_mismatch,
        COUNT(*) FILTER (WHERE ARR_DELAY >= 15 AND reason_sum = 0) AS delayed15_no_reason,
        COUNT(*) FILTER (WHERE ARR_DELAY IS NOT NULL AND ARR_DELAY < 15 AND reason_sum > 0) AS early_with_reason,
        COUNT(*) FILTER (WHERE (CANCELLED = 1 OR DIVERTED = 1) AND reason_sum > 0) AS cancelled_diverted_with_reason,
        ROUND(AVG(ABS(ARR_DELAY - reason_sum)) FILTER (WHERE ARR_DELAY IS NOT NULL AND ABS(ARR_DELAY - reason_sum) > 1), 2) AS avg_abs_diff_mismatch,
        MAX(ABS(ARR_DELAY - reason_sum)) AS max_abs_diff
    FROM x
    """
)
reason_distribution = rows(
    f"""
    SELECT reason, COUNT(*) AS rows, SUM(minutes) AS minutes,
           ROUND(100.0 * SUM(minutes) / SUM(SUM(minutes)) OVER (), 2) AS share_pct
    FROM (
        SELECT 'CARRIER' AS reason, DELAY_DUE_CARRIER AS minutes FROM f
        UNION ALL SELECT 'WEATHER', DELAY_DUE_WEATHER FROM f
        UNION ALL SELECT 'NAS', DELAY_DUE_NAS FROM f
        UNION ALL SELECT 'SECURITY', DELAY_DUE_SECURITY FROM f
        UNION ALL SELECT 'LATE_AIRCRAFT', DELAY_DUE_LATE_AIRCRAFT FROM f
    )
    WHERE COALESCE(minutes, 0) > 0
    GROUP BY 1 ORDER BY 3 DESC
    """
)
delayed_consistency = one(
    f"""
    WITH x AS (
        SELECT ARR_DELAY, ({reason_sum_expr}) AS reason_sum
        FROM f WHERE ARR_DELAY >= 15
    )
    SELECT COUNT(*) AS n,
           COUNT(*) FILTER (WHERE ABS(ARR_DELAY - reason_sum) <= 0.5) AS exact,
           COUNT(*) FILTER (WHERE ABS(ARR_DELAY - reason_sum) > 0.5) AS mismatch,
           MIN(ARR_DELAY - reason_sum) AS min_diff,
           MAX(ARR_DELAY - reason_sum) AS max_diff,
           ROUND(AVG(ABS(ARR_DELAY - reason_sum)), 2) AS avg_abs_diff
    FROM x
    """
)
reasons_per_flight = rows(
    """
    SELECT reason_count, COUNT(*) AS flights
    FROM (
        SELECT (CASE WHEN COALESCE(DELAY_DUE_CARRIER,0) > 0 THEN 1 ELSE 0 END
              + CASE WHEN COALESCE(DELAY_DUE_WEATHER,0) > 0 THEN 1 ELSE 0 END
              + CASE WHEN COALESCE(DELAY_DUE_NAS,0) > 0 THEN 1 ELSE 0 END
              + CASE WHEN COALESCE(DELAY_DUE_SECURITY,0) > 0 THEN 1 ELSE 0 END
              + CASE WHEN COALESCE(DELAY_DUE_LATE_AIRCRAFT,0) > 0 THEN 1 ELSE 0 END) AS reason_count
        FROM f WHERE ARR_DELAY >= 15
    )
    GROUP BY 1 ORDER BY 1
    """
)
cancellation_by_year = rows(
    """
    SELECT YEAR(CAST(FL_DATE AS DATE)) AS year,
           COALESCE(NULLIF(TRIM(CANCELLATION_CODE), ''), '(null)') AS code,
           COUNT(*) AS n
    FROM f GROUP BY 1, 2 ORDER BY 1, 3 DESC
    """
)
cancellation_codes = rows(
    """
    SELECT COALESCE(NULLIF(TRIM(CANCELLATION_CODE), ''), '(null)') AS code, COUNT(*) AS n
    FROM f GROUP BY 1 ORDER BY 2 DESC
    """
)
time_2400 = one(
    """
    SELECT SUM(CASE WHEN CRS_DEP_TIME = 2400 THEN 1 ELSE 0 END) AS crs_dep,
           SUM(CASE WHEN DEP_TIME = 2400 THEN 1 ELSE 0 END) AS dep,
           SUM(CASE WHEN WHEELS_OFF = 2400 THEN 1 ELSE 0 END) AS wheels_off,
           SUM(CASE WHEN WHEELS_ON = 2400 THEN 1 ELSE 0 END) AS wheels_on,
           SUM(CASE WHEN ARR_TIME = 2400 THEN 1 ELSE 0 END) AS arr,
           SUM(CASE WHEN CRS_ARR_TIME = 2400 THEN 1 ELSE 0 END) AS crs_arr
    FROM f
    """
)
origin_city_dups = rows(
    """
    SELECT ORIGIN AS code, COUNT(DISTINCT ORIGIN_CITY) AS n,
           STRING_AGG(DISTINCT ORIGIN_CITY, ' | ') AS names
    FROM f GROUP BY 1 HAVING COUNT(DISTINCT ORIGIN_CITY) > 1 ORDER BY 1
    """
)
dest_city_dups = rows(
    """
    SELECT DEST AS code, COUNT(DISTINCT DEST_CITY) AS n,
           STRING_AGG(DISTINCT DEST_CITY, ' | ') AS names
    FROM f GROUP BY 1 HAVING COUNT(DISTINCT DEST_CITY) > 1 ORDER BY 1
    """
)
carrier_code_dups = rows(
    "SELECT AIRLINE_CODE AS code, COUNT(DISTINCT AIRLINE) AS n FROM f GROUP BY 1 HAVING COUNT(DISTINCT AIRLINE) > 1"
)
carrier_name_dups = rows(
    "SELECT AIRLINE AS name, COUNT(DISTINCT AIRLINE_CODE) AS n FROM f GROUP BY 1 HAVING COUNT(DISTINCT AIRLINE_CODE) > 1"
)
carriers = rows(
    """
    SELECT AIRLINE_CODE AS carrier_code, MIN(AIRLINE) AS carrier_name,
           MIN(DOT_CODE) AS dot_code, COUNT(*) AS flights
    FROM f GROUP BY 1 ORDER BY 4 DESC
    """
)
route_distance_variation = one(
    """
    SELECT COUNT(*) AS routes_with_variation, COALESCE(MAX(n), 0) AS max_distinct
    FROM (
        SELECT ORIGIN, DEST, COUNT(DISTINCT DISTANCE) AS n
        FROM f GROUP BY 1, 2 HAVING COUNT(DISTINCT DISTANCE) > 1
    )
    """
)


def col_stats(c):
    return one(
        f"""
        SELECT '{c}' AS metric, COUNT({c}) AS n,
               ROUND(MIN({c}), 2) AS min, ROUND(quantile_cont({c}, 0.01), 2) AS p01,
               ROUND(quantile_cont({c}, 0.25), 2) AS p25, ROUND(quantile_cont({c}, 0.50), 2) AS p50,
               ROUND(quantile_cont({c}, 0.75), 2) AS p75, ROUND(quantile_cont({c}, 0.99), 2) AS p99,
               ROUND(MAX({c}), 2) AS max, ROUND(AVG({c}), 2) AS mean
        FROM f
        """
    )


stats = [col_stats(c) for c in ["ARR_DELAY", "DEP_DELAY", "TAXI_OUT", "TAXI_IN", "AIR_TIME", "ELAPSED_TIME", "DISTANCE"]]
outliers = {}
for c in ["ARR_DELAY", "DEP_DELAY"]:
    outliers[c] = one(
        f"""
        WITH q AS (SELECT quantile_cont({c}, 0.25) AS q1, quantile_cont({c}, 0.75) AS q3 FROM f)
        SELECT q1, q3, ROUND(q3 - q1, 2) AS iqr,
               SUM(CASE WHEN {c} < q1 - 1.5 * (q3 - q1) OR {c} > q3 + 1.5 * (q3 - q1) THEN 1 ELSE 0 END) AS n,
               ROUND(100.0 * SUM(CASE WHEN {c} < q1 - 1.5 * (q3 - q1) OR {c} > q3 + 1.5 * (q3 - q1) THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct
        FROM f, q GROUP BY 1, 2, 3
        """
    )
top_origins = rows(
    """
    SELECT ORIGIN AS code, COUNT(*) AS flights,
           ROUND(AVG(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 THEN ARR_DELAY END), 2) AS avg_arr_delay,
           ROUND(100.0 * SUM(CASE WHEN CANCELLED = 0 AND DIVERTED = 0 AND ARR_DELAY >= 15 THEN 1 ELSE 0 END) / COUNT(*), 2) AS delayed15_pct
    FROM f GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """
)

result = {
    "csv": {"path": CSV, "size_bytes": os.path.getsize(CSV), "types": types, "columns": cols},
    "total": total,
    "by_year_raw": by_year_raw,
    "filtered": filtered,
    "status": status,
    "status_by_year": status_by_year,
    "missing": missing,
    "empty_strings": empty,
    "distinct": {**distinct, "airports": airports["airports"]},
    "duplicates": {"business_key": duplicates, "business_key_plus_time": duplicates_with_time},
    "delay_reasons": {
        "per_reason": reason_stats,
        "total": reason_total,
        "consistency": consistency,
        "delayed_consistency": delayed_consistency,
        "reasons_per_flight": reasons_per_flight,
        "distribution": reason_distribution,
    },
    "status_share": {
        k: round(100.0 * v / status["n"], 2)
        for k, v in status.items() if k != "n"
    },
    "cancellation_by_year": cancellation_by_year,
    "cancellation_codes": cancellation_codes,
    "time_2400": time_2400,
    "origin_city_duplicates": origin_city_dups,
    "dest_city_duplicates": dest_city_dups,
    "carrier_code_duplicates": carrier_code_dups,
    "carrier_name_duplicates": carrier_name_dups,
    "carriers": carriers,
    "route_distance_variation": route_distance_variation,
    "stats": stats,
    "outliers": outliers,
    "top_origins": top_origins,
}

with open(OUT, "w", encoding="utf8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=2, default=str)

print(json.dumps({
    "total": total,
    "filtered": filtered,
    "status": status,
    "distinct": result["distinct"],
    "duplicates": result["duplicates"],
    "delay_reason_rows": reason_total["rows"],
    "delay_reason_minutes": reason_total["minutes"],
    "flights_with_reason": reason_total["flights_with_reason"],
    "consistency": consistency,
    "delayed_consistency": delayed_consistency,
    "reasons_per_flight": reasons_per_flight,
    "reason_distribution": reason_distribution,
    "status_share": result["status_share"],
    "cancellation_by_year": cancellation_by_year,
    "cancellation_codes": cancellation_codes,
    "time_2400": time_2400,
    "carrier_dups": {"code": carrier_code_dups, "name": carrier_name_dups},
    "city_dups": {"origin": origin_city_dups, "dest": dest_city_dups},
    "route_distance_variation": route_distance_variation,
    "outliers": outliers,
}, ensure_ascii=False, indent=2, default=str))
print("saved", OUT)
