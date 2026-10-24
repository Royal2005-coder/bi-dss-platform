-- HHMM extraction must not round minutes into the next hour.
-- Excess delay is defined above the agreed 15-minute threshold, preserving NULL.
select flight_key
from {{ ref('fact_flight') }}
where scheduled_dep_hour <> cast(floor(scheduled_dep_time / 100) as integer)
   or scheduled_dep_hour not between 0 and 23
   or scheduled_dep_minute not between 0 and 59
   or (arr_delay_minutes is null and excess_delay_minutes is not null)
   or (arr_delay_minutes is not null and (
       excess_delay_minutes is null
       or excess_delay_minutes <> greatest(arr_delay_minutes - 15, 0)))
