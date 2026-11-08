# -*- coding: utf-8 -*-
"""Generate notebooks/dss_delay_prediction.ipynb (8.4.2) - one source of truth: ../run_pipeline.py + ../src."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
cells = []


def md(text: str) -> None:
    cells.append({"cell_type": "markdown", "metadata": {}, "source": text.strip("\n")})


def code(text: str, tags: list[str] | None = None) -> None:
    meta = {"tags": tags} if tags else {}
    cells.append({"cell_type": "code", "metadata": meta, "execution_count": None, "outputs": [],
                  "source": text.strip("\n")})


md("""
# 8.4.2 — Mô hình dự báo trễ chuyến bay FAA ≥ 15 phút (SkyOps DSS)

**Bài toán.** Phân loại nhị phân: chuyến bay có hạ cánh trễ ≥ 15 phút so với lịch CRS hay không (định nghĩa FAA/BTS).
Kết quả là cột `Delay_Probability` (0.00–1.00) cho **toàn bộ 1.151.344 chuyến**, nạp vào **Power BI Desktop – Trang 5**.

**Giao thức (cố định trước khi xem tập test):**

| Hạng mục | Quy ước |
|---|---|
| Thời điểm dự báo | `T_pred = CRS_DEP − 60 phút` (DSS tác nghiệp cho trung tâm điều hành OCC) |
| Anti-Data Leakage | Mọi biến phải **đã biết tại `T_pred`**: lịch CRS, lịch sử ≤ D-1, lịch sử tàu bay (chặng inbound), trạng thái sân bay/mạng lưới trước `T_pred`. Cấm mọi biến hậu sự kiện của chính chuyến bay. |
| Chia tập theo thời gian | Train 01/2022–04/2023 · Validation 05–06/2023 · Test 07–08/2023 |
| Chọn mô hình | Chỉ theo **validation**; test chỉ dùng để báo cáo |
| Mô hình | Ensemble XGBoost (GPU) + LightGBM → hiệu chỉnh Isotonic |
| Hạ tầng | Google Colab GPU (free T4) hoặc cụm Velura RTX 4060; seed 42 |

Notebook gọi đúng mã nguồn trong `ml/delay_8_4/src` (repo `bi-dss-platform`) nên kết quả trùng với pipeline dòng lệnh `run_pipeline.py`.
""")

md("## 0. Cấu hình môi trường")
code("""
# Tham so (papermill co the ghi de) -----------------------------------------
FORCE_REBUILD = True          # True: dung lai toan bo feature tu warehouse.duckdb (full cycle)
AUDIT_SAMPLE = 400            # so chuyen kiem tra leakage doc lap
REPO_URL = "https://github.com/Royal2005-coder/bi-dss-platform.git"
DRIVE_DIR = "/content/drive/MyDrive/SkyOps_8_4"   # Colab: thu muc chua warehouse.duckdb, nhan CSV
""", tags=["parameters"])
code("""
import os, sys, subprocess, json, time, platform, hashlib, shutil
from pathlib import Path

IN_COLAB = "google.colab" in sys.modules
if IN_COLAB:
    from google.colab import drive
    drive.mount("/content/drive")
    if not Path("/content/bi-dss-platform").exists():
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, "/content/bi-dss-platform"], check=True)
    ML_DIR = Path("/content/bi-dss-platform/ml/delay_8_4")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ML_DIR / "requirements.txt")], check=True)
    DB_PATH = Path(DRIVE_DIR) / "warehouse.duckdb"
else:
    ML_DIR = Path.cwd().parent if (Path.cwd().parent / "run_pipeline.py").exists() else Path.cwd()
    DB_PATH = Path(os.environ.get("BI_DUCKDB_PATH", ML_DIR / "data" / "warehouse.duckdb"))
WORK_DIR, OUT_DIR = ML_DIR / "work", ML_DIR / "outputs"
sys.path.insert(0, str(ML_DIR / "src"))
os.environ.update(BI_DUCKDB_PATH=str(DB_PATH), BI_WORK_DIR=str(WORK_DIR), BI_OUT_DIR=str(OUT_DIR),
                  BI_FEATURE_SPEC=str(OUT_DIR / "models" / "feature_spec_v14.json"))
print("ML_DIR :", ML_DIR)
print("DB     :", DB_PATH, "exists =", DB_PATH.exists(), f"({DB_PATH.stat().st_size/1e6:.0f} MB)" if DB_PATH.exists() else "")
print("Python :", platform.python_version(), "|", platform.platform())
""")
code("""
# GPU: Colab T4 / Velura RTX 4060. XGBoost dung device='cuda'; LightGBM chay CPU da luong.
import xgboost as xgb, lightgbm as lgb, sklearn, duckdb, numpy as np, pandas as pd
print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv"],
                     capture_output=True, text=True).stdout or "Khong thay GPU NVIDIA -> chay CPU")
try:
    xgb.train({"tree_method": "hist", "device": "cuda"}, xgb.DMatrix(np.zeros((4, 1)), label=[0, 1, 0, 1]), 1)
    print("XGBoost CUDA: OK")
except Exception as e:
    print("XGBoost CUDA khong kha dung:", e)
print("xgboost", xgb.__version__, "| lightgbm", lgb.__version__, "| sklearn", sklearn.__version__, "| duckdb", duckdb.__version__)
""")
code("""
def run_step(*args):
    \"\"\"Chay run_pipeline.py va in log theo thoi gian thuc.\"\"\"
    cmd = [sys.executable, "-u", str(ML_DIR / "run_pipeline.py"), "--db", str(DB_PATH),
           "--work", str(WORK_DIR), "--out", str(OUT_DIR), *args]
    t0 = time.time()
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8")) as p:
        for line in p.stdout:
            if "val-auc" in line or "valid_0" in line:
                continue  # bo bot log tung vong boosting
            print(line, end="")
    print(f"-> exit {p.returncode} sau {time.time()-t0:.0f}s")
    assert p.returncode == 0, "pipeline loi"
""")

md("""
## 1. Dữ liệu nguồn (kho DuckDB của mục 8.1)

Nguồn: `flights_sample_3m.csv` (BTS, mẫu ngẫu nhiên ~10% các chuyến nội địa Mỹ) → dbt-duckdb → `marts.fact_flight` (1.151.344 chuyến, 01/2022–08/2023).
Biến mục tiêu `target = is_arr_delayed15` chỉ xác định với chuyến **hoàn thành** (không hủy/chuyển hướng); chuyến hủy vẫn được chấm điểm vì tại `T_pred` chưa ai biết nó sẽ bị hủy.
""")
code("""
con = duckdb.connect(str(DB_PATH), read_only=True)
overview = con.execute(\"\"\"
    SELECT CASE WHEN date_key <= 20230430 THEN '1-train' WHEN date_key <= 20230630 THEN '2-validation' ELSE '3-test' END AS split,
           MIN(date_key) AS tu_ngay, MAX(date_key) AS den_ngay, COUNT(*) AS so_chuyen,
           SUM(is_completed) AS hoan_thanh, ROUND(AVG(CASE WHEN is_completed = 1 THEN is_arr_delayed15 END), 4) AS ty_le_tre15
    FROM marts.fact_flight GROUP BY 1 ORDER BY 1\"\"\").fetchdf()
con.close()
overview
""")

md("""
## 2. Feature engineering tiền sự kiện (DuckDB / Python)

| Nhóm | Ví dụ | Mốc thời gian được phép |
|---|---|---|
| Lịch CRS & lịch sử ≤ D-1 | giờ cất cánh KH, khoảng cách, mật độ lịch, tỷ lệ trễ trước đó theo hãng/tuyến/sân bay/giờ (cửa sổ kết thúc ở `1 PRECEDING`) | trước ngày bay |
| Thời tiết ngày bay | mưa, gió giật, tầm nhìn, mã thời tiết tại sân bay đi/đến (Open-Meteo) | dự báo ngày (proxy) |
| Lịch sử tàu bay (proxy) | chặng inbound cùng hãng + số hiệu: thời gian quay đầu KH, độ trễ đã biết, cận dưới độ trễ | sự kiện đã xảy ra trước `T_pred` |
| Nowcast sân bay | số chuyến đã cất cánh, trễ TB, backlog, hủy tại sân bay đi/đến trong 60–180 phút | ≤ `T_pred` (giờ địa phương) |
| Nowcast mạng lưới | tỷ lệ trễ toàn quốc / theo hãng / vùng / hãng×vùng trong 60–720 phút | ≤ `T_pred` (đồng hồ **UTC**, múi giờ IANA) |

Bước 1–4 của pipeline:
""")
code("""
# Buoc 1-4: feature tien su kien. FORCE_REBUILD=True -> dung lai tu warehouse.duckdb (full cycle).
run_step("--from-step", "1", "--to-step", "4", *(["--force"] if FORCE_REBUILD else []))
""")
code("""
f8 = pd.read_parquet(WORK_DIR / "features_v8.parquet", columns=["flight_key", "date_key", "target"])
nc = pd.read_parquet(WORK_DIR / "nowcast_h60.parquet")
net = pd.read_parquet(WORK_DIR / "network_h60_v2.parquet")
print(f"features_v8: {f8.shape} | nowcast_h60: {nc.shape} | network_h60: {net.shape}")
print(f"Ty le chuyen co chang inbound cung so hieu (proxy tau bay): {nc.has_inbound.mean():.3%}"
      "  <- thap vi du lieu la mau ~10% nen chuoi quay vong bi dut")
print(f"So chuyen toan quoc da cat canh trong 120' truoc T_pred (TB): {net.nat_dep_n_120.mean():.0f}; theo hang: {net.car_dep_n_120.mean():.1f}")
net.describe().T[["mean", "std", "min", "max"]].head(12)
""")

md("""
## 3. R&D có kiểm soát — vì sao chọn kiến trúc này

Ablation: cùng learner (XGBoost GPU), cùng seed và cùng split; **chỉ thay đổi nhóm biến**, nên chênh lệch AUC quy được cho nhóm biến đó.
Các bước chọn đều dựa trên validation; test được ghi lại để minh bạch.
""")
code("""
rnd = ML_DIR / "reports" / "rnd"
a = json.load(open(rnd / "experiment_v11_ablation.json")); n = json.load(open(rnd / "experiment_v12_network_h60.json"))
t = json.load(open(rnd / "experiment_v13_hl180.json")); base = json.load(open(rnd / "baseline_v10_model_comparison.json"))
rows = [("v10 (bàn giao cũ) LGBM+XGB rank", base["ensemble_rank_normalized"]["val"]["auc"], base["ensemble_rank_normalized"]["test"]["auc"], "D-1, ensemble theo thứ hạng")]
lab = {"A0_D-1_strict": "A0 · CRS + lịch sử ≤ D-1", "A1_D-1_weather": "A1 · + thời tiết ngày bay",
       "B1_T-60_aircraft": "B1 · + lịch sử tàu bay (inbound) @T-60", "B2_T-60_full": "B2 · + nowcast sân bay @T-60",
       "B1_T-120_aircraft": "B1 @T-120", "B2_T-120_full": "B2 @T-120"}
for k, v in lab.items():
    rows.append((v, a[k]["val_auc"], a[k]["test_auc"], f"{a[k]['n_feat']} biến"))
rows.append(("B3 · + nowcast mạng lưới (UTC)", n["B3_T-60_network"]["val_auc"], n["B3_T-60_network"]["test_auc"], f"{n['B3_T-60_network']['n_feat']} biến"))
for k, v in [("C1_xgb", "C1 · mạng lưới v2 (hãng×vùng, 60–720')"), ("C2_xgb_recency", "C2 · + trọng số gần đây (loại)"),
             ("L_lgb", "LightGBM cùng bộ biến"), ("E_rank_avg", "Ensemble XGB + LGBM")]:
    rows.append((v, t[k]["val"]["auc"], t[k]["test"]["auc"], ""))
abl = pd.DataFrame(rows, columns=["Thí nghiệm", "Val AUC", "Test AUC", "Ghi chú"]).round(4)
abl
""")

md("""
## 4. Huấn luyện mô hình cuối (v14) trên GPU

- **Stage 1 (chọn):** XGBoost GPU (siêu tham số tốt nhất từ random search trên validation) + LightGBM, early stopping trên validation; trung bình xác suất → **Isotonic** (fit trên validation) → ngưỡng tối ưu F1 trên validation.
- **Stage 2 (triển khai):** refit trên train + validation với số vòng × 1,15; giữ nguyên bộ hiệu chỉnh và ngưỡng của stage 1.
- Sửa look-ahead: quy mô hub sân bay tính lại chỉ trên kỳ train.
""")
code("""
run_step("--from-step", "5", "--to-step", "5")
""")
code("""
S = json.load(open(OUT_DIR / "model_summary_v14.json", encoding="utf-8"))
s1, s2 = S["stage1_selection"], S["stage2_production"]
def row(name, m): return {"Mô hình": name, "ROC-AUC": m["auc"], "PR-AUC": m["ap"], "Brier": m["brier"], "Ngưỡng": m["threshold"],
                          "Precision": m["precision"], "Recall": m["recall"], "F1": m["f1"], "Accuracy": m["accuracy"],
                          "TN": m["tn"], "FP": m["fp"], "FN": m["fn"], "TP": m["tp"]}
res = pd.DataFrame([row("Stage 1 · Validation", s1["val"]), row("Stage 1 · Test", s1["test"]),
                    row("Stage 2 (triển khai) · Test", s2["test"]), row("Stage 2 · Test @0.5", s2["test_at_0.5"])]).round(4)
print("Test AUC stage 2 - CI95 bootstrap:", [round(x, 4) for x in s2["test_auc_ci95"]])
print("AUC theo thang (test):", {k: round(v, 4) for k, v in S["test_auc_by_month"].items()})
res
""")
code("""
from IPython.display import Image, display
for f in ["roc_curve", "feature_importance", "confusion_matrix", "precision_recall_curve", "calibration_curve", "lift_decile", "ablation_auc"]:
    display(Image(filename=str(OUT_DIR / "figures" / f"{f}.png"), width=640))
""")
code("""
imp = pd.read_csv(OUT_DIR / "feature_importance_v14.csv")
print(imp.groupby("family").gain_share.sum().sort_values(ascending=False).round(3))
imp.head(20).round(4)
""")

md("""
## 5. Kiểm định Anti-Data Leakage độc lập

Bước 6–7: `audit_leakage.py` **không dùng lại SQL** sinh feature: tính lại bằng pandas brute-force số chuyến/tỷ lệ trễ theo hãng và toàn quốc trong 120 phút trước `T_pred` cho mẫu ngẫu nhiên, rồi so khớp; đồng thời kiểm tra danh sách biến cấm, cửa sổ lịch sử và cờ chặng inbound.
""")
code("""
run_step("--from-step", "6")
audit = json.load(open(OUT_DIR / "leakage_audit_v14.json", encoding="utf-8"))
print(json.dumps(audit, ensure_ascii=False, indent=1))
assert audit["all_passed"], "AUDIT LEAKAGE THAT BAI"
""")
code("""
# Tu dien bien tien su kien (du 100% bien dua vao mo hinh) - buoc 7 cua pipeline
vd = pd.read_csv(OUT_DIR / "variable_dictionary_pre_event_v14.csv")
print(vd.groupby("Nhom").agg(so_bien=("Bien", "size"), gain_pct=("Gain_share_pct", "sum")).sort_values("gain_pct", ascending=False))
vd.sort_values("Gain_share_pct", ascending=False).head(15)
""")

md("## 6. Xuất `DSS_Flight_Prediction.csv` cho Power BI Trang 5")
code("""
csv = OUT_DIR / "DSS_Flight_Prediction.csv"
pred = pd.read_csv(csv, nrows=None, usecols=["FlightKey", "Delay_Probability", "Priority_Tier", "DatasetSplit", "IsLabelled"])
assert len(pred) == 1_151_344 and pred.FlightKey.is_unique
assert pred.Delay_Probability.between(0, 1).all()
print(f"{len(pred):,} dong | Delay_Probability [{pred.Delay_Probability.min():.4f}, {pred.Delay_Probability.max():.4f}]")
print(pred.Priority_Tier.value_counts().sort_index().to_dict())
print("SHA-256:", S["csv"]["sha256"], "|", f"{csv.stat().st_size/1e6:.1f} MB")
display(pd.read_csv(OUT_DIR / "DSS_Priority_Tier.csv"))
pd.read_csv(OUT_DIR / "DSS_Flight_Prediction_sample.csv").head(8)
""")
code("""
if IN_COLAB:  # tai len Drive cho nhom Power BI
    os.makedirs(DRIVE_DIR, exist_ok=True)
    for f in ["DSS_Flight_Prediction.csv", "DSS_Priority_Tier.csv", "model_summary_v14.json", "leakage_audit_v14.json"]:
        shutil.copy(OUT_DIR / f, Path(DRIVE_DIR) / f)
    print("Da copy len", DRIVE_DIR)
""")

md("""
## 7. Phản biện & giới hạn (model card rút gọn)

1. **Mục tiêu ROC-AUC ≥ 0,75**: đạt trên validation; trên test (07–08/2023) thấp hơn khoảng 0,01, nhất quán ở mọi thí nghiệm → do **dịch phân phối mùa hè** (tháng 7 đạt ≥ 0,75, tháng 8 giảm). Không tinh chỉnh theo test để "chạm" ngưỡng.
2. **Trần thông tin của dữ liệu mẫu 10%**: không có `TAIL_NUM`, chuỗi quay vòng tàu bay chỉ nối được ~2% chuyến; các tín hiệu nowcast bị loãng ~10 lần so với dữ liệu BTS đầy đủ.
3. **Thời tiết** là giá trị quan trắc của ngày bay (Open-Meteo archive) — giả định "dự báo hoàn hảo"; ablation cho thấy nhóm này đóng góp ~+0,017 AUC. Triển khai thật cần thay bằng bản tin dự báo (Open-Meteo Historical Forecast / NOAA).
4. **Hiệu chỉnh**: xác suất isotonic, kẹp [0,001; 0,999]; train/validation rows trong CSV là dự báo trong mẫu (in-sample) — chỉ dùng kỳ test để đánh giá trên dashboard.
5. **Vận hành**: theo dõi AUC theo tháng trên Trang 5, retrain định kỳ (drift tháng 8).
""")
code("""
manifest = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "env": S["environment"],
            "inputs": {"warehouse.duckdb_bytes": DB_PATH.stat().st_size},
            "outputs": {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((OUT_DIR).glob("*.csv"))}}
json.dump(manifest, open(OUT_DIR / "run_manifest_v14.json", "w"), indent=1)
manifest
""")

nb = {"cells": cells, "metadata": {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.11"},
    "accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"}},
    "nbformat": 4, "nbformat_minor": 5}
for i, c in enumerate(nb["cells"]):
    c["id"] = f"c{i:02d}"
out = os.path.join(HERE, "dss_delay_prediction.ipynb")
json.dump(nb, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("wrote", out, len(cells), "cells")
