WITH expected AS (
    SELECT COUNT(*) AS n FROM {{ ref('int_flights_2022_2023') }}
),
actual AS (
    SELECT COUNT(*) AS n FROM {{ ref('fact_flight') }}
)
SELECT expected.n AS expected_rows, actual.n AS actual_rows
FROM expected, actual
WHERE expected.n <> actual.n
