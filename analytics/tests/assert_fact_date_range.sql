SELECT *
FROM {{ ref('fact_flight') }}
WHERE date_key < 20220101 OR date_key > 20231231
