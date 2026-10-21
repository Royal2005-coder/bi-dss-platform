{{ config(materialized='table') }}

WITH routes AS (
    SELECT
        origin_code,
        dest_code,
        ROUND(AVG(distance), 2) AS distance_miles
    FROM {{ ref('int_flights_2022_2023') }}
    GROUP BY origin_code, dest_code
)
SELECT
    CAST(ROW_NUMBER() OVER (ORDER BY r.origin_code, r.dest_code) AS INTEGER) AS route_key,
    r.origin_code,
    r.dest_code,
    r.origin_code || '-' || r.dest_code AS route_name,
    o.airport_key AS origin_airport_key,
    d.airport_key AS dest_airport_key,
    r.distance_miles,
    CASE
        WHEN r.distance_miles < 500 THEN '0–499 dặm'
        WHEN r.distance_miles < 1000 THEN '500–999 dặm'
        WHEN r.distance_miles < 1500 THEN '1000–1499 dặm'
        WHEN r.distance_miles < 2000 THEN '1500–1999 dặm'
        ELSE '2000+ dặm'
    END AS distance_group,
    CASE
        WHEN r.distance_miles < 500 THEN 1
        WHEN r.distance_miles < 1000 THEN 2
        WHEN r.distance_miles < 1500 THEN 3
        WHEN r.distance_miles < 2000 THEN 4
        ELSE 5
    END AS distance_group_order
FROM routes r
JOIN {{ ref('dim_airport') }} o
    ON r.origin_code = o.airport_code
JOIN {{ ref('dim_airport') }} d
    ON r.dest_code = d.airport_code
