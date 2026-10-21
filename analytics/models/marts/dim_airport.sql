{{ config(materialized='table') }}

WITH codes AS (
    SELECT origin_code AS airport_code FROM {{ ref('int_flights_2022_2023') }}
    UNION
    SELECT dest_code AS airport_code FROM {{ ref('int_flights_2022_2023') }}
)
SELECT
    CAST(ROW_NUMBER() OVER (ORDER BY c.airport_code) AS INTEGER) AS airport_key,
    c.airport_code,
    COALESCE(a.airport_name, c.airport_code) AS airport_name,
    COALESCE(a.city, '') AS city,
    COALESCE(a.state_code, '') AS state_code,
    COALESCE(a.state_name, '') AS state_name,
    COALESCE(a.region, 'Other') AS region,
    COALESCE(a.region_name, 'Khác') AS region_name,
    COALESCE(a.country_code, 'US') AS country_code,
    a.latitude,
    a.longitude,
    COALESCE(a.data_source, 'missing') AS data_source
FROM codes c
LEFT JOIN {{ ref('airport_master') }} a
    ON c.airport_code = a.airport_code
