{{ config(materialized='table') }}

WITH classified AS (
    SELECT
        *,
        CASE
            WHEN cancelled = 1 THEN 'Cancelled'
            WHEN diverted = 1 THEN 'Diverted'
            WHEN arr_delay >= 15 THEN 'Delayed'
            ELSE 'On-time'
        END AS flight_status_code
    FROM {{ ref('int_flights_2022_2023') }}
),
keyed AS (
    SELECT
        *,
        CAST(STRFTIME(flight_date, '%Y%m%d') AS INTEGER) AS date_key,
        ROW_NUMBER() OVER (
            ORDER BY flight_date, carrier_code, flight_number, origin_code, dest_code
        ) AS flight_key
    FROM classified
)
SELECT
    k.flight_key,
    k.date_key,
    c.carrier_key,
    o.airport_key AS origin_airport_key,
    d.airport_key AS dest_airport_key,
    r.route_key,
    fs.flight_status_key,
    k.flight_number,
    CASE WHEN k.crs_dep_time = 2400 THEN 0 ELSE k.crs_dep_time END AS scheduled_dep_time,
    CAST(FLOOR((CASE WHEN k.crs_dep_time = 2400 THEN 0 ELSE k.crs_dep_time END) / 100) AS INTEGER) AS scheduled_dep_hour,
    CAST(MOD(CASE WHEN k.crs_dep_time = 2400 THEN 0 ELSE k.crs_dep_time END, 100) AS INTEGER) AS scheduled_dep_minute,
    k.cancellation_code,
    k.dep_delay,
    k.arr_delay,
    CASE
        WHEN k.cancelled = 0 AND k.dep_delay IS NOT NULL
        THEN GREATEST(k.dep_delay, 0)
    END AS dep_delay_minutes,
    CASE
        WHEN k.cancelled = 0 AND k.diverted = 0 AND k.arr_delay IS NOT NULL
        THEN GREATEST(k.arr_delay, 0)
    END AS arr_delay_minutes,
    k.taxi_out AS taxi_out_minutes,
    k.taxi_in AS taxi_in_minutes,
    k.air_time AS air_time_minutes,
    k.elapsed_time AS elapsed_time_minutes,
    k.crs_elapsed_time AS crs_elapsed_time_minutes,
    k.distance AS distance_miles,
    CASE WHEN k.cancelled = 0 AND k.dep_delay >= 15 THEN 1 ELSE 0 END AS is_dep_delayed15,
    CASE WHEN k.cancelled = 0 AND k.diverted = 0 AND k.arr_delay >= 15 THEN 1 ELSE 0 END AS is_arr_delayed15,
    k.cancelled AS is_cancelled,
    k.diverted AS is_diverted,
    CASE WHEN k.cancelled = 0 AND k.diverted = 0 THEN 1 ELSE 0 END AS is_completed,
    CASE
        WHEN GREATEST(
            COALESCE(k.delay_due_carrier, 0),
            COALESCE(k.delay_due_weather, 0),
            COALESCE(k.delay_due_nas, 0),
            COALESCE(k.delay_due_security, 0),
            COALESCE(k.delay_due_late_aircraft, 0)
        ) > 0 THEN 1 ELSE 0
    END AS has_delay_reason,
    k.delay_due_carrier,
    k.delay_due_weather,
    k.delay_due_nas,
    k.delay_due_security,
    k.delay_due_late_aircraft
FROM keyed k
JOIN {{ ref('dim_carrier') }} c
    ON k.carrier_code = c.carrier_code
JOIN {{ ref('dim_airport') }} o
    ON k.origin_code = o.airport_code
JOIN {{ ref('dim_airport') }} d
    ON k.dest_code = d.airport_code
JOIN {{ ref('dim_route') }} r
    ON k.origin_code = r.origin_code AND k.dest_code = r.dest_code
JOIN {{ ref('dim_flight_status') }} fs
    ON k.flight_status_code = fs.flight_status_code
