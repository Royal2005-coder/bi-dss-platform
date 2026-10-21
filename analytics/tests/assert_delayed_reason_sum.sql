WITH delayed AS (
    SELECT
        flight_key,
        arr_delay,
        COALESCE(delay_due_carrier, 0)
        + COALESCE(delay_due_weather, 0)
        + COALESCE(delay_due_nas, 0)
        + COALESCE(delay_due_security, 0)
        + COALESCE(delay_due_late_aircraft, 0) AS reason_sum
    FROM {{ ref('int_flight_enriched') }}
    WHERE arr_delay >= 15
)
SELECT *
FROM delayed
WHERE ABS(arr_delay - reason_sum) > 0.5
