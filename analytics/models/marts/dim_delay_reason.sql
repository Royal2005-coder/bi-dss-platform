{{ config(materialized='table') }}

SELECT * FROM (
    SELECT 1 AS delay_reason_key, 'CARRIER' AS delay_reason_code,
           'Hãng hàng không' AS delay_reason_name,
           'Chậm do hãng vận chuyển (Carrier Delay)' AS description,
           'DELAY_DUE_CARRIER' AS source_column, 1 AS sort_order
    UNION ALL
    SELECT 2, 'WEATHER', 'Thời tiết',
           'Chậm do điều kiện thời tiết cực đoan (Weather Delay)',
           'DELAY_DUE_WEATHER', 2
    UNION ALL
    SELECT 3, 'NAS', 'Hệ thống không lưu',
           'Chậm do hệ thống kiểm soát không lưu (NAS Delay)',
           'DELAY_DUE_NAS', 3
    UNION ALL
    SELECT 4, 'SECURITY', 'An ninh',
           'Chậm do an ninh hàng không (Security Delay)',
           'DELAY_DUE_SECURITY', 4
    UNION ALL
    SELECT 5, 'LATE_AIRCRAFT', 'Tàu bay đến muộn',
           'Chậm do tàu bay của chuyến trước đến muộn (Late Aircraft Delay)',
           'DELAY_DUE_LATE_AIRCRAFT', 5
) t
