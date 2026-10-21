SELECT *
FROM {{ ref('int_flight_enriched') }}
WHERE COALESCE(delay_due_carrier, 0) < 0
   OR COALESCE(delay_due_weather, 0) < 0
   OR COALESCE(delay_due_nas, 0) < 0
   OR COALESCE(delay_due_security, 0) < 0
   OR COALESCE(delay_due_late_aircraft, 0) < 0
