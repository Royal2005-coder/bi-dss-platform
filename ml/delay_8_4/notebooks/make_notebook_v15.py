# -*- coding: utf-8 -*-
"""Generate notebooks/dss_delay_prediction_v15.ipynb - Colab GPU (T4) first, also runs on Velura/local.

Single source of truth: ../run_pipeline.py --version v15 and ../src. Checkpoints live on Google Drive so a
Colab disconnect resumes instead of restarting.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BRANCH = "feat/8.4-dss-delay-model-v14"
cells = []


def md(t: str) -> None:
    cells.append({"cell_type": "markdown", "metadata": {}, "source": t.strip("\n")})


def code(t: str, tags=None) -> None:
    cells.append({"cell_type": "code", "metadata": {"tags": tags} if tags else {}, "execution_count": None,
                  "outputs": [], "source": t.strip("\n")})


md(f"""
# 8.4.2 v15 — Dự báo trễ FAA ≥ 15 phút, 100% tiền sự kiện (Colab GPU)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Royal2005-coder/bi-dss-platform/blob/{BRANCH}/ml/delay_8_4/notebooks/dss_delay_prediction_v15.ipynb)

**Cách chạy trên Colab:** Runtime → Change runtime type → **T4 GPU** → Run all.
Cần sẵn `warehouse.duckdb` (kho DuckDB của mục 8.1, ~570 MB) trong `MyDrive/SkyOps_8_4/`.

| Hạng mục | v15 |
|---|---|
| Thời điểm dự báo | `T_pred = CRS_DEP − 60 phút` |
| Biến | lịch CRS, lịch sử từ 2019 (≤ D-1), tàu bay chặng trước, nowcast sân bay + mạng lưới (UTC), **thời tiết quan trắc theo giờ ≤ T_pred − 60'** (Meteostat) |
| Train | 2019 + 2021 + 01/2022–04/2023 (bỏ 2020 COVID: không cải thiện validation) |
| Validation / Test | 05–06/2023 / 07–08/2023 — test chấm theo **walk-forward hằng tháng** |
| Mô hình | XGBoost GPU + CatBoost GPU (+ LightGBM nếu bật) → Isotonic hiệu chỉnh cuốn chiếu |
| Lớp DSS | ngưỡng Bayes theo chi phí, luật hard-stop (Wilson ≥ 0,8), năng lực OCC theo hãng×giờ, conformal thích nghi, mã lý do SHAP |
""")

md("## 0. Tham số & môi trường")
code("""
LEARNERS = "xgb,cat"         # Colab free chi 2 vCPU: LightGBM (CPU) rat cham. Velura 28 core: "xgb,lgb,cat"
TRAIN_YEARS = "2019,2021,2022,2023"
SMOKE = False                # True = chay nhanh tren 2023 de kiem tra notebook (~5 phut)
DRIVE_DIR = "/content/drive/MyDrive/SkyOps_8_4"
BRANCH = "feat/8.4-dss-delay-model-v14"
REPO_URL = "https://github.com/Royal2005-coder/bi-dss-platform.git"
""", tags=["parameters"])
code("""
import os, sys, subprocess, json, time, platform, shutil
from pathlib import Path
IN_COLAB = "google.colab" in sys.modules
if IN_COLAB:
    from google.colab import drive
    drive.mount("/content/drive")
    repo = Path("/content/bi-dss-platform")
    if not repo.exists():
        subprocess.run(["git", "clone", "--depth", "1", "-b", BRANCH, REPO_URL, str(repo)], check=True)
    else:
        subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"], check=True)
    ML_DIR = repo / "ml" / "delay_8_4"
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ML_DIR / "requirements.txt"), "catboost"], check=True)
    DB_PATH = Path(DRIVE_DIR) / "warehouse.duckdb"
    WORK_DIR = Path("/content/work_v15")                       # dung lai feature nhanh, khong can luu
    OUT_DIR = Path(DRIVE_DIR) / ("outputs_v15_smoke" if SMOKE else "outputs_v15")  # checkpoint + ket qua tren Drive
else:
    ML_DIR = Path.cwd().parent if (Path.cwd().parent / "run_pipeline.py").exists() else Path.cwd()
    DB_PATH = Path(os.environ.get("BI_DUCKDB_PATH", ML_DIR / "data" / "warehouse.duckdb"))
    WORK_DIR = Path(os.environ.get("BI_WORK_DIR", ML_DIR / "work_v15"))
    OUT_DIR = Path(os.environ.get("BI_OUT_DIR", ML_DIR / ("outputs_v15_smoke" if SMOKE else "outputs_v15")))
assert DB_PATH.exists(), f"Khong thay {DB_PATH} - hay upload warehouse.duckdb len {DRIVE_DIR}"
mem_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9 if hasattr(os, "sysconf") else float("nan")
os.environ.update(BI_DUCKDB_PATH=str(DB_PATH), BI_WORK_DIR=str(WORK_DIR), BI_OUT_DIR=str(OUT_DIR),
                  BI_LEARNERS=LEARNERS, BI_TRAIN_YEARS="2023" if SMOKE else TRAIN_YEARS,
                  BI_DUCKDB_MEM="6GB" if mem_gb > 11 else "3GB", PYTHONIOENCODING="utf-8")
print("ML_DIR", ML_DIR, "| DB", DB_PATH, f"{DB_PATH.stat().st_size/1e6:.0f} MB | OUT", OUT_DIR, f"| RAM {mem_gb:.1f} GB")
print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv"], capture_output=True, text=True).stdout)
""")
code("""
def run_step(*args):
    cmd = [sys.executable, "-u", str(ML_DIR / "run_pipeline.py"), "--version", "v15", "--db", str(DB_PATH),
           "--work", str(WORK_DIR), "--out", str(OUT_DIR), *args]
    t0 = time.time()
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                          errors="replace", env=os.environ.copy()) as p:
        for line in p.stdout:
            if line.startswith("[") and ("]\\tv" in line or "valid_0" in line):
                continue
            print(line, end="")
    print(f"-> exit {p.returncode} sau {time.time()-t0:.0f}s")
    assert p.returncode == 0, "pipeline loi"
""")

md("""
## 1. Dữ liệu & feature tiền sự kiện (bước 1–5)

1. Thời tiết theo giờ Meteostat (UTC) cho ~360 sân bay, 2019–2023.
2. `features_all`: lịch CRS, lịch, mật độ lịch, tỷ lệ trễ lịch sử 14 nhóm (cửa sổ kết thúc ở `1 PRECEDING`), lần khai thác trước của số hiệu chuyến.
3. Nowcast sân bay + chặng inbound cùng số hiệu (proxy tàu bay), chỉ sự kiện đã biết ≤ `T_pred`.
4. Nowcast mạng lưới toàn quốc / hãng / vùng / hãng×vùng trên đồng hồ UTC (múi giờ IANA).
5. Thời tiết nowcast: quan trắc ≤ `T_pred − 60'` tại sân bay đi/đến + số hub có dông.
""")
code("""
run_step("--from-step", "1", "--to-step", "5")
""")

md("""
## 2. Huấn luyện + walk-forward + lớp DSS (bước 6)

Stage 1 chọn ensemble trên validation; tháng 7 dùng mô hình train đến 30/06, tháng 8 dùng mô hình train đến 31/07
(hiệu chỉnh isotonic bằng dự báo ngoài mẫu của kỳ liền trước). Checkpoint trên Drive: nếu Colab ngắt, chạy lại ô này sẽ tiếp tục.
""")
code("""
run_step("--from-step", "6", "--to-step", "6")
""")
code("""
import pandas as pd
S = json.load(open(OUT_DIR / "model_summary_v15.json", encoding="utf-8"))
st1, wf, stat = S["stage1"], S["walk_forward_test"], S["static_test"]
rows = [("Validation (stage 1)", st1["val"]), ("Test – mô hình cố định", stat["test"]), ("Test – walk-forward hằng tháng", wf["test"])]
res = pd.DataFrame([{"Tập": n, "ROC-AUC": m["auc"], "PR-AUC": m["ap"], "Brier": m["brier"], "Precision": m["precision"],
                     "Recall": m["recall"], "F1": m["f1"], "Ngưỡng": m["threshold"]} for n, m in rows]).round(4)
print("Learner val AUC:", {k: round(v, 4) for k, v in st1["val_auc_by_learner"].items()}, "| ensemble:", st1["members"])
print("Walk-forward: thang 7 =", round(wf["test_auc_jul"], 4), "| thang 8 =", round(wf["test_auc_aug"], 4),
      "| CI95 =", [round(x, 4) for x in wf["test_auc_ci95"]])
res
""")

md("## 3. Lớp quyết định DSS trên tập test")
code("""
d = S["dss_policy_test"]
print("Conformal: tinh", round(d["conformal_static_coverage"], 3), "-> thich nghi (ACI)", round(d["conformal_aci_coverage"], 3),
      "| ty le bat dinh", round(d["conformal"]["uncertain_share"], 3), "| AUC tren nhom chac chan", d["conformal"]["auc_on_confident"])
print("Hard-stop: ty le", d["hard_stop"]["share"], "precision", d["hard_stop"]["precision"])
print(pd.DataFrame(d["hard_stop"]["rules"]).T[["val_support", "val_precision", "val_wilson_lb95", "accepted"]])
print("P1 (can thiep):", {k: round(v, 3) for k, v in d["p1_act"].items() if v is not None})
pd.DataFrame(d["capacity_curve"]).round(3)
""")

md("## 4. Kiểm định leakage độc lập (bước 7) & xuất CSV cho Power BI")
code("""
run_step("--from-step", "7", "--to-step", "7")
audit = json.load(open(OUT_DIR / "leakage_audit_v15.json", encoding="utf-8"))
print(json.dumps({k: v.get("passed") for k, v in audit.items() if isinstance(v, dict)}, indent=1), "all_passed =", audit["all_passed"])
csv = OUT_DIR / "DSS_Flight_Prediction.csv"
pred = pd.read_csv(csv, usecols=["FlightKey", "Delay_Probability", "Priority_Tier", "DSS_Decision", "Confidence", "Reason_1", "DatasetSplit"])
assert pred.FlightKey.is_unique and pred.Delay_Probability.between(0, 1).all()
print(f"{len(pred):,} dong | {csv.stat().st_size/1e6:.0f} MB | SHA-256 {S['csv']['sha256']}")
print(pred.Priority_Tier.value_counts().sort_index().to_dict())
pd.read_csv(OUT_DIR / "DSS_Flight_Prediction_sample.csv").head(8)
""")

md("""
## 5. Ghi chú phản biện
- Thời tiết là **quan trắc trước T_pred** (không còn thời tiết cả ngày như v14) → không có thông tin tương lai.
- Train/validation rows trong CSV: validation là ngoài mẫu (stage 1), train 2022–04/2023 là trong mẫu; đánh giá trên dashboard nên lọc `DatasetSplit = test`.
- Kết quả có thể lệch rất nhỏ giữa GPU T4 / RTX 4060 / CPU do khác phiên bản thư viện.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python", "version": "3.11"},
                                   "accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"}},
      "nbformat": 4, "nbformat_minor": 5}
for i, c in enumerate(nb["cells"]):
    c["id"] = f"v15c{i:02d}"
out = os.path.join(HERE, "dss_delay_prediction_v15.ipynb")
json.dump(nb, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("wrote", out, len(cells), "cells")
