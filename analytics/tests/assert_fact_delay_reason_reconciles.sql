WITH fact AS (
    SELECT SUM(delay_minutes) AS minutes FROM {{ ref('fact_delay_reason') }}
),
source AS (
    SELECT SUM(
        COALESCE(delay_due_carrier, 0)
        + COALESCE(delay_due_weather, 0)
        + COALESCE(delay_due_nas, 0)
        + COALESCE(delay_due_security, 0)
        + COALESCE(delay_due_late_aircraft, 0)
    ) AS minutes
    FROM {{ ref('int_flight_enriched') }}
)
SELECT fact.minutes AS fact_minutes, source.minutes AS source_minutes
FROM fact, source
WHERE ABS(fact.minutes - source.minutes) > 0.5
