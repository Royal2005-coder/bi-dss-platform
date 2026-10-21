WITH expected AS (
    SELECT SUM(
        CASE WHEN COALESCE(delay_due_carrier, 0) > 0 THEN 1 ELSE 0 END
        + CASE WHEN COALESCE(delay_due_weather, 0) > 0 THEN 1 ELSE 0 END
        + CASE WHEN COALESCE(delay_due_nas, 0) > 0 THEN 1 ELSE 0 END
        + CASE WHEN COALESCE(delay_due_security, 0) > 0 THEN 1 ELSE 0 END
        + CASE WHEN COALESCE(delay_due_late_aircraft, 0) > 0 THEN 1 ELSE 0 END
    ) AS n
    FROM {{ ref('int_flight_enriched') }}
),
actual AS (
    SELECT COUNT(*) AS n FROM {{ ref('fact_delay_reason') }}
)
SELECT expected.n AS expected_rows, actual.n AS actual_rows
FROM expected, actual
WHERE expected.n <> actual.n
