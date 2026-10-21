SELECT flight_key, delay_reason_key, COUNT(*) AS n
FROM {{ ref('fact_delay_reason') }}
GROUP BY 1, 2
HAVING COUNT(*) > 1
