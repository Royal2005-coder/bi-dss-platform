{{ config(materialized='table') }}

SELECT *
FROM {{ ref('stg_flights') }}
WHERE flight_date BETWEEN DATE '2022-01-01' AND DATE '2023-12-31'
