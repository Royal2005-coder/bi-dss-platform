{{ config(materialized='table') }}

WITH days AS (
    SELECT CAST(gs AS DATE) AS full_date
    FROM GENERATE_SERIES(DATE '2022-01-01', DATE '2023-12-31', INTERVAL 1 DAY) AS t(gs)
)
SELECT
    CAST(STRFTIME(full_date, '%Y%m%d') AS INTEGER) AS date_key,
    full_date,
    CAST(EXTRACT(YEAR FROM full_date) AS INTEGER) AS year,
    CAST(EXTRACT(QUARTER FROM full_date) AS INTEGER) AS quarter,
    'Q' || CAST(CAST(EXTRACT(QUARTER FROM full_date) AS INTEGER) AS VARCHAR) AS quarter_name,
    CAST(EXTRACT(MONTH FROM full_date) AS INTEGER) AS month,
    'Tháng ' || CAST(CAST(EXTRACT(MONTH FROM full_date) AS INTEGER) AS VARCHAR) AS month_name,
    STRFTIME(full_date, '%Y-%m') AS month_year,
    CAST(EXTRACT(DAY FROM full_date) AS INTEGER) AS day_of_month,
    CAST(EXTRACT(DOY FROM full_date) AS INTEGER) AS day_of_year,
    CAST(EXTRACT(WEEK FROM full_date) AS INTEGER) AS week_of_year,
    CAST(EXTRACT(ISODOW FROM full_date) AS INTEGER) AS weekday_number,
    CASE EXTRACT(ISODOW FROM full_date)
        WHEN 1 THEN 'Thứ Hai'
        WHEN 2 THEN 'Thứ Ba'
        WHEN 3 THEN 'Thứ Tư'
        WHEN 4 THEN 'Thứ Năm'
        WHEN 5 THEN 'Thứ Sáu'
        WHEN 6 THEN 'Thứ Bảy'
        WHEN 7 THEN 'Chủ Nhật'
    END AS weekday_name,
    CASE WHEN EXTRACT(ISODOW FROM full_date) IN (6, 7) THEN TRUE ELSE FALSE END AS is_weekend,
    CASE
        WHEN EXTRACT(MONTH FROM full_date) IN (3, 4, 5) THEN 'Xuân'
        WHEN EXTRACT(MONTH FROM full_date) IN (6, 7, 8) THEN 'Hạ'
        WHEN EXTRACT(MONTH FROM full_date) IN (9, 10, 11) THEN 'Thu'
        ELSE 'Đông'
    END AS season
FROM days
