# 8.4.2 — Dự báo trễ chuyến bay FAA ≥ 15 phút (SkyOps DSS)

Mô hình phân loại nhị phân chấm `Delay_Probability` (0,001–0,999, đã hiệu chỉnh) cho toàn bộ 1.151.344 chuyến
trong kho DuckDB của mục 8.1, nạp vào **Power BI Desktop – Trang 5**.

## Kết quả chính thức (Velura RTX 4060, chạy full-cycle bằng notebook)

| | ROC-AUC | PR-AUC | Precision | Recall | F1 |
|---|---|---|---|---|---|
| Validation 05–06/2023 | **0,7523** | 0,490 | 0,425 | 0,605 | 0,499 |
| Test 07–08/2023 (bản triển khai, refit) | **0,7443** (CI95 0,7413–0,7473) | 0,515 | 0,413 | 0,693 | 0,517 |

Ngưỡng quyết định 0,25 (tối ưu F1 trên validation). Test tháng 7: 0,7515 · tháng 8: 0,7276 (drift mùa hè).
Chạy độc lập trên RTX 3050 / Windows: test AUC 0,7442. Số liệu đầy đủ: `reports/official/model_summary_v14.json`.

## Giao thức

- **Thời điểm dự báo** `T_pred = CRS_DEP − 60 phút`. Mọi biến phải đã biết tại `T_pred`.
- **Biến** (280): lịch CRS, lịch sử ≤ D-1, thời tiết ngày bay, lịch sử tàu bay (chặng inbound cùng số hiệu),
  nowcast sân bay, nowcast mạng lưới (toàn quốc / hãng / vùng, đồng hồ UTC theo múi giờ IANA).
  Từ điển: `reports/official/variable_dictionary_pre_event_v14.csv`.
- **Chia tập theo thời gian**: train ≤ 30/04/2023 · validation 05–06/2023 · test 07–08/2023. Chọn mô hình chỉ theo validation.
- **Mô hình**: XGBoost (CUDA) + LightGBM → trung bình xác suất → Isotonic; refit train+validation với số cây × 1,15.
- **Audit leakage độc lập** (`src/audit_leakage.py`): tính lại bằng pandas brute-force, khớp 100%, 0 sự kiện tương lai.

## Chạy lại

```bash
pip install -r requirements.txt
python run_pipeline.py --db /path/to/warehouse.duckdb          # 7 bước, ~10 phút trên RTX 4060
```

Notebook: `notebooks/dss_delay_prediction.ipynb` (Colab GPU free hoặc Velura). Trên Colab đặt `warehouse.duckdb`
vào `MyDrive/SkyOps_8_4/`; notebook tự clone repo, cài thư viện, chạy pipeline và copy CSV lên Drive.
Bản đã thực thi kèm output: `notebooks/dss_delay_prediction_executed.ipynb`.
Notebook được sinh từ `notebooks/make_notebook.py` — sửa generator, không sửa tay `.ipynb`.

| Bước | Script | Đầu ra |
|---|---|---|
| 1 | `src/features_base.py` | `work/features.parquet` |
| 2 | `src/features_v8.py` | `work/features_v8.parquet` |
| 3 | `src/build_nowcast_features.py` | `work/nowcast_h60.parquet` |
| 4 | `src/build_network_nowcast.py` | `work/network_h60_v2.parquet` |
| 5 | `src/train_final_v14.py` | `outputs/` CSV, models, figures, summary |
| 6 | `src/audit_leakage.py` | `outputs/leakage_audit_v14.json` |
| 7 | `src/make_variable_dictionary.py` | `outputs/variable_dictionary_pre_event_v14.csv` |

## Thư mục

- `models/` — `xgb_v14.json`, `lgb_v14.txt`, `isotonic_v14.joblib`, `feature_spec_v14.json` (biến, mã categorical, ngưỡng).
- `reports/official/` — metrics, audit, hình, mẫu CSV, log chạy chính thức.
- `reports/rnd/` — ablation v11–v13, random search, log; script tái hiện: `src/experiment_v1*.py`.
- `data/weather_daily.parquet` — thời tiết ngày theo sân bay (Open-Meteo archive, `src/fetch_weather.py`).

`DSS_Flight_Prediction.csv` (~140 MB) không lưu trong Git; lấy từ Google Drive của nhóm hoặc chạy lại pipeline.

## Giới hạn

1. Thời tiết là quan trắc của ngày bay (giả định dự báo hoàn hảo, đóng góp ~+0,017 AUC).
2. Dữ liệu là mẫu ~10% BTS, không có `TAIL_NUM`: chỉ ~2% chuyến nối được chặng inbound.
3. Dòng train/validation trong CSV là dự báo trong mẫu; đánh giá trên dashboard nên lọc `DatasetSplit = test`.
4. Test AUC thấp hơn mục tiêu 0,75 khoảng 0,006; không tinh chỉnh theo test.
