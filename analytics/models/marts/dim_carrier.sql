{{ config(materialized='table') }}

WITH carriers AS (
    SELECT
        carrier_code,
        MIN(carrier_name) AS carrier_name,
        MIN(dot_code) AS dot_code,
        MIN(airline_dot) AS airline_dot
    FROM {{ ref('int_flights_2022_2023') }}
    GROUP BY carrier_code
)
SELECT
    CAST(ROW_NUMBER() OVER (ORDER BY carrier_code) AS INTEGER) AS carrier_key,
    carrier_code,
    carrier_name,
    CAST(dot_code AS INTEGER) AS dot_code,
    airline_dot
FROM carriers
