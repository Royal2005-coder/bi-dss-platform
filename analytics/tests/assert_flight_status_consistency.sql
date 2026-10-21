SELECT
    f.flight_key,
    s.flight_status_code,
    f.is_cancelled,
    f.is_diverted,
    f.is_arr_delayed15,
    f.is_completed
FROM {{ ref('fact_flight') }} f
JOIN {{ ref('dim_flight_status') }} s
    ON f.flight_status_key = s.flight_status_key
WHERE
    (s.flight_status_code = 'Cancelled' AND f.is_cancelled <> 1)
    OR (s.flight_status_code = 'Diverted' AND f.is_diverted <> 1)
    OR (s.flight_status_code = 'Delayed' AND f.is_arr_delayed15 <> 1)
    OR (s.flight_status_code = 'On-time' AND (f.is_cancelled <> 0 OR f.is_diverted <> 0 OR f.is_arr_delayed15 <> 0))
    OR (f.is_completed = 1 AND f.is_cancelled = 1)
    OR (f.is_completed = 1 AND f.is_diverted = 1)
