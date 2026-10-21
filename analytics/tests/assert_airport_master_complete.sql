SELECT *
FROM {{ ref('airport_master') }}
WHERE airport_code IS NULL
   OR airport_name IS NULL OR TRIM(airport_name) = ''
   OR state_code IS NULL OR TRIM(state_code) = ''
   OR state_name IS NULL OR TRIM(state_name) = ''
   OR region IS NULL OR TRIM(region) = '' OR region = 'Other'
   OR latitude IS NULL
   OR longitude IS NULL
