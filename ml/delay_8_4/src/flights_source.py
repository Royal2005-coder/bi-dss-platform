# -*- coding: utf-8 -*-
"""Single flight source for the v15 feature builders (2019-01-01..2023-08-31, staging.stg_flights).

flight_key keeps the 8.1 warehouse key for 2022-2023 (ROW_NUMBER over the same ordering as
intermediate.int_flights_2022_2023 / marts.fact_flight) and is negative for 2019-2021 history rows, so
Power BI joins are unchanged. Set BI_FLIGHT_SCOPE=2022 to reproduce the v14 scope exactly.
"""
from __future__ import annotations

import os
from datetime import date

SCOPE = os.environ.get("BI_FLIGHT_SCOPE", "all")
EPOCH = date(2019, 1, 1) if SCOPE == "all" else date(2022, 1, 1)
END = date(2023, 8, 31)
ORDER = "flight_date, carrier_code, flight_number, origin_code, dest_code"


def flights_sql() -> str:
    """SELECT returning every staging column plus flight_key, restricted to the configured scope."""
    if SCOPE == "all":
        return f"""
            SELECT CAST(CASE WHEN flight_date >= DATE '2022-01-01'
                             THEN ROW_NUMBER() OVER (PARTITION BY flight_date >= DATE '2022-01-01' ORDER BY {ORDER})
                             ELSE -ROW_NUMBER() OVER (PARTITION BY flight_date >= DATE '2022-01-01' ORDER BY {ORDER})
                        END AS BIGINT) AS flight_key, *
            FROM staging.stg_flights
            WHERE flight_date BETWEEN DATE '{EPOCH}' AND DATE '{END}'"""
    return f"""
        SELECT CAST(ROW_NUMBER() OVER (ORDER BY {ORDER}) AS BIGINT) AS flight_key, *
        FROM intermediate.int_flights_2022_2023"""
