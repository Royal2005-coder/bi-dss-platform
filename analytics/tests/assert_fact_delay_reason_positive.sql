SELECT *
FROM {{ ref('fact_delay_reason') }}
WHERE delay_minutes <= 0 OR delay_minutes IS NULL
