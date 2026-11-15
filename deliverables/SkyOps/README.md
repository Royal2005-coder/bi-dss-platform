# SkyOps – Gói nộp đồ án BI & DSS

| Thư mục | Nội dung |
|---|---|
| `00_Data/` | Hai bảng DSS (`DSS_Flight_Prediction.csv` qua Git LFS, `DSS_Priority_Tier.csv`). Tám bảng Dim/Fact nằm ở `SkyOps_Technical/data/processed/` (`FactFlight.csv` qua Git LFS). |
| `01_Solution/SkyOps.pbix` | Mô hình dữ liệu và 5 trang dashboard (Git LFS) |
| `02_Data_Model_Documentation/` | Đặc tả Star Schema, từ điển dữ liệu, 26 measure DAX, 12 quan hệ |
| `04_Test_UAT_Report/` | Báo cáo đối soát (9.1) và kiểm thử giao diện, hiệu năng (9.2.2) |
| `07_Runbook/` | Word sơ đồ cây thư mục và Runbook 4 bước, cùng script dựng |

Chưa có trong repo: Quick User Guide, biên bản UAT Phụ lục D, Limitations & Known Issues, slide thuyết trình.

## Runbook triển khai
1. Cài Power BI Desktop.
2. Đặt 10 tệp CSV vào `C:\SkyOps\00_Data` (lấy từ `SkyOps_Technical/data/processed/` và `00_Data/`; chạy `git lfs pull`).
3. Home → Transform data → Data source settings → Change Source cho từng nguồn về `C:\SkyOps\00_Data\<tên>.csv`.
4. Apply changes, rồi Refresh → Data. Kết quả đúng: FactFlight 1.151.344 dòng, FactDelay 397.131 dòng.

Lưu ý: `DSS_Flight_Prediction.csv` trong PBIX là kết quả mô hình v14; kết quả v15 nằm ở `ml/delay_8_4/reports/official/model_summary_v15.json`.
