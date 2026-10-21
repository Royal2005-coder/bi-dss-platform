{{ config(materialized='table') }}

WITH unpivoted AS (
    SELECT flight_key, date_key, carrier_key, origin_airport_key,
           'CARRIER' AS delay_reason_code, delay_due_carrier AS delay_minutes
    FROM {{ ref('int_flight_enriched') }}
    UNION ALL
    SELECT flight_key, date_key, carrier_key, origin_airport_key,
           'WEATHER', delay_due_weather
    FROM {{ ref('int_flight_enriched') }}
    UNION ALL
    SELECT flight_key, date_key, carrier_key, origin_airport_key,
           'NAS', delay_due_nas
    FROM {{ ref('int_flight_enriched') }}
    UNION ALL
    SELECT flight_key, date_key, carrier_key, origin_airport_key,
           'SECURITY', delay_due_security
    FROM {{ ref('int_flight_enriched') }}
    UNION ALL
    SELECT flight_key, date_key, carrier_key, origin_airport_key,
           'LATE_AIRCRAFT', delay_due_late_aircraft
    FROM {{ ref('int_flight_enriched') }}
)
SELECT
    CAST(ROW_NUMBER() OVER (ORDER BY u.flight_key, u.delay_reason_code) AS BIGINT) AS delay_fact_key,
    u.flight_key,
    u.date_key,
    u.carrier_key,
    u.origin_airport_key,
    d.delay_reason_key,
    CAST(u.delay_minutes AS DOUBLE) AS delay_minutes
FROM unpivoted u
JOIN {{ ref('dim_delay_reason') }} d
    ON u.delay_reason_code = d.delay_reason_code
WHERE u.delay_minutes > 0
