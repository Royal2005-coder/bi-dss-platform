{{ config(materialized='table') }}

SELECT * FROM (
    SELECT 1 AS flight_status_key, 'On-time' AS flight_status_code,
           'Đúng giờ' AS flight_status_name,
           'Chuyến bay hạ cánh sớm hơn hoặc chậm dưới 15 phút so với lịch' AS description,
           TRUE AS is_completed, FALSE AS is_disrupted, 1 AS sort_order
    UNION ALL
    SELECT 2, 'Delayed', 'Chậm chuyến',
           'Chuyến bay hạ cánh chậm từ 15 phút trở lên so với lịch',
           TRUE, TRUE, 2
    UNION ALL
    SELECT 3, 'Cancelled', 'Hủy chuyến',
           'Chuyến bay bị hủy và không thực hiện hành trình',
           FALSE, TRUE, 3
    UNION ALL
    SELECT 4, 'Diverted', 'Chuyển hướng',
           'Chuyến bay chuyển hướng, không hạ cánh tại sân bay dự kiến',
           FALSE, TRUE, 4
) t
