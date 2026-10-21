SELECT
    flight_date,
    carrier_code,
    flight_number,
    origin_code,
    dest_code,
    COUNT(*) AS n
FROM {{ ref('int_flights_2022_2023') }}
GROUP BY ALL
HAVING COUNT(*) > 1
