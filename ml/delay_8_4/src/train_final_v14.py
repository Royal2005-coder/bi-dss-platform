# -*- coding: utf-8 -*-
"""8.4.2 final model (v14): T-60 operational delay classifier, protocol fixed BEFORE looking at test.

Protocol
  1. Features: v8 (CRS + history <= D-1 + daily weather) + aircraft-history proxy (inbound leg)
     + airport nowcast + network nowcast, all known at T_pred = CRS_DEP - 60 min.
     Look-ahead fix: hub size recomputed on the training window only.
  2. Stage 1 (selection): fit XGBoost GPU + LightGBM on train (<= 2023-04-30), early-stop on
     validation (2023-05..06). Ensemble = mean of probabilities. Fit isotonic calibration and the
     F1-optimal threshold on validation.
  3. Stage 2 (production): refit both learners on train+validation with the stage-1 iteration
     counts x 1.15, apply the stage-1 calibrator and threshold.
  4. Test (2023-07..08) is scored once for stage 1 and stage 2; never used for any choice.
  5. Score every flight (cancelled/diverted included: unknown at T_pred); evaluate on labelled ones.

Env: BI_WORK_DIR (features dir), BI_OUT_DIR (artifacts dir). Usage: python train_final_v14.py
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from datetime import datetime, timezone

import joblib
import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import sklearn  # noqa: E402
import xgboost as xgb  # noqa: E402
from sklearn.calibration import calibration_curve  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score, brier_score_loss, confusion_matrix, f1_score, log_loss,
    precision_recall_curve, precision_score, recall_score, roc_auc_score, roc_curve,
)

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(HERE, "..", "work"))
OUT = os.environ.get("BI_OUT_DIR", os.path.join(HERE, "..", "outputs"))
SEED = 42
H = 60
TRAIN_END, VAL_START, VAL_END, TEST_START = 20230430, 20230501, 20230630, 20230701
REFIT_SCALE = 1.15
PROB_CLIP = (0.001, 0.999)  # isotonic saturates at exact 0/1 in sparse tails
CATEGORICAL = ["carrier_code", "origin", "dest", "origin_state", "dest_state",
               "origin_region", "dest_region", "season", "distance_group"]
NON_FEATURES = {"flight_key", "date_key", "carrier_key", "origin_airport_key", "dest_airport_key",
                "route_key", "flight_number", "full_date", "flight_date_str", "arr_delay",
                "is_arr_delayed15", "is_completed", "is_cancelled", "is_diverted", "target",
                "carrier_name"}
XGB_BASE = dict(objective="binary:logistic", eval_metric="auc", tree_method="hist", eta=0.03,
                max_depth=6, min_child_weight=100.0, colsample_bytree=0.4, subsample=0.7,
                reg_lambda=10.0, gamma=2.0, max_bin=256, seed=SEED)
LGB_PARAMS = dict(objective="binary", metric="auc", learning_rate=0.03, num_leaves=127,
                  min_child_samples=200, feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=20.0, max_bin=255, verbosity=-1, seed=SEED, n_jobs=-1)


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def gpu_available() -> bool:
    try:
        xgb.train(dict(tree_method="hist", device="cuda"), xgb.DMatrix(np.zeros((4, 1)), label=[0, 1, 0, 1]), 1)
        return True
    except xgb.core.XGBoostError:
        return False


def load_features() -> tuple[pd.DataFrame, list[str], dict[str, list[str]]]:
    df = pd.read_parquet(os.path.join(WORK, "features_v8.parquet"))
    groups = {}
    for name, fn in (("nowcast", f"nowcast_h{H}.parquet"), ("network", f"network_h{H}_v2.parquet")):
        part = pd.read_parquet(os.path.join(WORK, fn))
        cols = [c for c in part.columns if c != "flight_key"]
        groups[name] = cols
        df = df.merge(part.astype({c: "float32" for c in cols}), on="flight_key", how="left",
                      validate="one_to_one")
    # look-ahead fix: hub size from the training window only
    tr = df[df.date_key <= TRAIN_END]
    for side in ("origin", "dest"):
        hub = tr.groupby(side).size().rename(f"{side}_hub_flights")
        df = df.drop(columns=f"{side}_hub_flights").merge(hub, left_on=side, right_index=True, how="left")
        df[f"{side}_hub_flights"] = df[f"{side}_hub_flights"].fillna(0)
    df = df.sort_values("flight_key").reset_index(drop=True)
    cat_maps = {}
    for c in CATEGORICAL:
        cats = df[c].astype("category")
        cat_maps[c] = list(map(str, cats.cat.categories))
        df[c] = cats.cat.codes.astype("int32")
    num = df.select_dtypes(include=["float64", "int64", "bool"]).columns.difference(["flight_key", "date_key"])
    df[num] = df[num].astype("float32")
    feats = [c for c in df.columns if c not in NON_FEATURES]
    return df, feats, {"categorical_codes": cat_maps, **groups}


def best_xgb_params() -> dict:
    path = _rnd_path("experiment_v13b_search.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            best = json.load(fh)[0]["params"]
        return dict(XGB_BASE, **best)
    return dict(XGB_BASE)


def metrics(y: np.ndarray, p: np.ndarray, thr: float) -> dict:
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred).ravel()
    return dict(n=int(len(y)), positives=int(y.sum()), auc=float(roc_auc_score(y, p)),
                ap=float(average_precision_score(y, p)), brier=float(brier_score_loss(y, p)),
                logloss=float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))), threshold=float(thr),
                tn=int(tn), fp=int(fp), fn=int(fn), tp=int(tp),
                precision=float(precision_score(y, pred, zero_division=0)),
                recall=float(recall_score(y, pred, zero_division=0)),
                f1=float(f1_score(y, pred, zero_division=0)), accuracy=float((tp + tn) / len(y)))


def bootstrap_auc(y: np.ndarray, p: np.ndarray, n_boot: int = 500) -> list[float]:
    rng = np.random.default_rng(SEED)
    vals = [roc_auc_score(y[idx], p[idx]) for idx in (rng.integers(0, len(y), len(y)) for _ in range(n_boot))]
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    grid = np.round(np.arange(0.05, 0.951, 0.005), 3)
    return float(grid[int(np.argmax([f1_score(y, p >= t) for t in grid]))])


class Ensemble:
    """XGBoost + LightGBM probability average, then isotonic calibration."""

    def __init__(self, xgb_params: dict, device: str) -> None:
        self.xgb_params = dict(xgb_params, device=device)
        self.xgb_model: xgb.Booster | None = None
        self.lgb_model: lgb.Booster | None = None
        self.xgb_iter = self.lgb_iter = 0
        self.calibrator: IsotonicRegression | None = None

    def fit_select(self, X_tr, y_tr, X_va, y_va) -> None:
        dtr = xgb.QuantileDMatrix(X_tr, y_tr, max_bin=self.xgb_params["max_bin"])
        dva = xgb.QuantileDMatrix(X_va, y_va, ref=dtr)
        self.xgb_model = xgb.train(self.xgb_params, dtr, 10000, evals=[(dva, "val")],
                                   early_stopping_rounds=300, verbose_eval=500)
        self.xgb_iter = self.xgb_model.best_iteration + 1
        ltr = lgb.Dataset(X_tr, y_tr)
        lva = lgb.Dataset(X_va, y_va, reference=ltr)
        self.lgb_model = lgb.train(LGB_PARAMS, ltr, 10000, valid_sets=[lva],
                                   callbacks=[lgb.early_stopping(300, verbose=False), lgb.log_evaluation(500)])
        self.lgb_iter = self.lgb_model.best_iteration

    def fit_refit(self, X, y) -> None:
        d = xgb.QuantileDMatrix(X, y, max_bin=self.xgb_params["max_bin"])
        self.xgb_model = xgb.train(self.xgb_params, d, int(self.xgb_iter * REFIT_SCALE))
        self.xgb_iter = self.xgb_model.num_boosted_rounds()
        self.lgb_model = lgb.train(LGB_PARAMS, lgb.Dataset(X, y), int(self.lgb_iter * REFIT_SCALE))
        self.lgb_iter = self.lgb_model.current_iteration()

    def raw(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        px = self.xgb_model.predict(xgb.DMatrix(X), iteration_range=(0, self.xgb_iter))
        pl = self.lgb_model.predict(X, num_iteration=self.lgb_iter)
        return px, pl

    def score(self, X: pd.DataFrame) -> np.ndarray:
        px, pl = self.raw(X)
        return (px + pl) / 2

    def proba(self, X: pd.DataFrame) -> np.ndarray:
        return np.clip(self.calibrator.predict(self.score(X)), *PROB_CLIP)


def plot_all(fig_dir: str, y_va, p_va, y_te, p_te, thr, imp: pd.DataFrame, ablation: list[dict]) -> None:
    os.makedirs(fig_dir, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    # ROC
    fig, ax = plt.subplots(figsize=(5.6, 5))
    for y, p, lab, col in ((y_va, p_va, "Validation 05–06/2023", "#8c8c8c"), (y_te, p_te, "Test 07–08/2023", "#1f4e79")):
        f, t, _ = roc_curve(y, p)
        ax.plot(f, t, color=col, lw=2, label=f"{lab} (AUC = {roc_auc_score(y, p):.3f})")
    ax.plot([0, 1], [0, 1], ls="--", color="#bbbbbb", lw=1)
    ax.set(xlabel="False Positive Rate", ylabel="True Positive Rate", title="ROC – Ensemble T-60 (v14)")
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout(); fig.savefig(os.path.join(fig_dir, "roc_curve.png"), dpi=200); plt.close(fig)
    # PR
    fig, ax = plt.subplots(figsize=(5.6, 5))
    pr, rc, _ = precision_recall_curve(y_te, p_te)
    ax.plot(rc, pr, color="#1f4e79", lw=2, label=f"Test (AP = {average_precision_score(y_te, p_te):.3f})")
    ax.axhline(y_te.mean(), ls="--", color="#bbbbbb", lw=1, label=f"Tỷ lệ nền = {y_te.mean():.3f}")
    ax.set(xlabel="Recall", ylabel="Precision", title="Precision–Recall – Test")
    ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "precision_recall_curve.png"), dpi=200); plt.close(fig)
    # confusion
    cm = confusion_matrix(y_te, (p_te >= thr).astype(int))
    fig, ax = plt.subplots(figsize=(4.8, 4.2))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, f"{v:,}".replace(",", "."), ha="center", va="center",
                color="white" if v > cm.max() / 2 else "black", fontsize=12)
    ax.set_xticks([0, 1], ["Dự báo: Đúng giờ", "Dự báo: Trễ"]); ax.set_yticks([0, 1], ["Thực tế: Đúng giờ", "Thực tế: Trễ"])
    ax.set_title(f"Confusion Matrix – Test (ngưỡng {thr:.3f})"); fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "confusion_matrix.png"), dpi=200); plt.close(fig)
    # calibration
    fig, ax = plt.subplots(figsize=(5.2, 5))
    fr, mp = calibration_curve(y_te, p_te, n_bins=15, strategy="quantile")
    ax.plot(mp, fr, "o-", color="#1f4e79", label="Test")
    ax.plot([0, 1], [0, 1], ls="--", color="#bbbbbb")
    ax.set(xlabel="Delay_Probability dự báo", ylabel="Tỷ lệ trễ thực tế", title="Calibration (isotonic) – Test")
    ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "calibration_curve.png"), dpi=200); plt.close(fig)
    # feature importance
    top = imp.head(20).iloc[::-1]
    colors = {"Lịch CRS & lịch sử ≤ D-1": "#9dc3e6", "Thời tiết ngày bay": "#70ad47",
              "Lịch sử tàu bay (inbound)": "#ed7d31", "Nowcast sân bay T-60": "#ffc000",
              "Nowcast mạng lưới T-60": "#1f4e79"}
    fig, ax = plt.subplots(figsize=(7.5, 6.2))
    ax.barh(top.feature, top.gain_share * 100, color=[colors[g] for g in top.family])
    ax.set(xlabel="Tỷ trọng gain (%) – trung bình XGBoost & LightGBM", title="Top 20 Feature Importance (v14)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colors.values()]
    ax.legend(handles, colors.keys(), frameon=False, loc="lower right", fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(fig_dir, "feature_importance.png"), dpi=200); plt.close(fig)
    # lift by decile
    dec = pd.qcut(pd.Series(p_te).rank(method="first"), 10, labels=False)
    rate = pd.Series(y_te).groupby(dec).mean()
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(np.arange(1, 11), rate.values * 100, color="#1f4e79")
    ax.axhline(y_te.mean() * 100, ls="--", color="#c00000", label="Tỷ lệ trễ trung bình")
    ax.set(xlabel="Thập phân vị rủi ro (10 = cao nhất)", ylabel="Tỷ lệ trễ thực tế (%)", title="Lift theo thập phân vị – Test")
    ax.set_xticks(range(1, 11)); ax.legend(frameon=False); fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "lift_decile.png"), dpi=200); plt.close(fig)
    # ablation
    fig, ax = plt.subplots(figsize=(8, 4.2))
    names = [a["label"] for a in ablation]
    ax.bar(names, [a["val_auc"] for a in ablation], width=0.38, align="edge", color="#9dc3e6", label="Validation")
    ax.bar(names, [a["test_auc"] for a in ablation], width=-0.38, align="edge", color="#1f4e79", label="Test")
    ax.axhline(0.75, ls="--", color="#c00000", lw=1, label="Mục tiêu 0.75")
    ax.set_ylim(0.69, 0.765); ax.set_ylabel("ROC-AUC"); ax.set_title("Ablation theo nhóm biến tiền sự kiện")
    plt.setp(ax.get_xticklabels(), rotation=18, ha="right", fontsize=8)
    ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper left"); fig.tight_layout()
    fig.savefig(os.path.join(fig_dir, "ablation_auc.png"), dpi=200); plt.close(fig)


def family(col: str, groups: dict) -> str:
    if col in groups["network"]:
        return "Nowcast mạng lưới T-60"
    if col in groups["nowcast"]:
        inbound = {"has_inbound", "sched_turn_min", "in_status_known", "in_dep_delay_known",
                   "in_cancel_known", "in_delay_lb", "turn_slack_lb"}
        return "Lịch sử tàu bay (inbound)" if col in inbound else "Nowcast sân bay T-60"
    if "_wx_" in col or col.startswith("wx_"):
        return "Thời tiết ngày bay"
    return "Lịch CRS & lịch sử ≤ D-1"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    device = "cuda" if gpu_available() else "cpu"
    log(f"device={device} xgb={xgb.__version__} lgb={lgb.__version__} sklearn={sklearn.__version__}")
    df, feats, groups = load_features()
    y_all = df["target"].to_numpy()
    lab = ~np.isnan(y_all)
    dk = df["date_key"].to_numpy()
    tr, va, te = lab & (dk <= TRAIN_END), lab & (dk >= VAL_START) & (dk <= VAL_END), lab & (dk >= TEST_START)
    log(f"rows={len(df)} labelled={lab.sum()} train={tr.sum()} val={va.sum()} test={te.sum()} features={len(feats)}")
    X = df[feats]

    # ---- stage 1: selection on validation ----
    ens = Ensemble(best_xgb_params(), device)
    ens.fit_select(X[tr], y_all[tr], X[va], y_all[va])
    s_va = ens.score(X[va])
    ens.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(s_va, y_all[va])
    p_va = np.clip(ens.calibrator.predict(s_va), *PROB_CLIP)
    thr = f1_threshold(y_all[va], p_va)
    px_va, pl_va = ens.raw(X[va])
    px_te1, pl_te1 = ens.raw(X[te])
    p_te1 = np.clip(ens.calibrator.predict((px_te1 + pl_te1) / 2), *PROB_CLIP)
    stage1 = {"xgb_best_iter": ens.xgb_iter, "lgb_best_iter": ens.lgb_iter,
              "val": metrics(y_all[va], p_va, thr), "test": metrics(y_all[te], p_te1, thr),
              "val_auc_xgb": float(roc_auc_score(y_all[va], px_va)),
              "val_auc_lgb": float(roc_auc_score(y_all[va], pl_va)),
              "test_auc_xgb": float(roc_auc_score(y_all[te], px_te1)),
              "test_auc_lgb": float(roc_auc_score(y_all[te], pl_te1))}
    log(f"stage1 val AUC={stage1['val']['auc']:.4f} test AUC={stage1['test']['auc']:.4f} thr={thr}")
    imp_x = pd.Series(ens.xgb_model.get_score(importance_type="gain")).reindex(feats).fillna(0)
    imp_l = pd.Series(ens.lgb_model.feature_importance("gain"), index=feats)
    imp = pd.DataFrame({"feature": feats,
                        "gain_share": (imp_x / imp_x.sum() + imp_l / imp_l.sum()).values / 2})
    imp["family"] = [family(c, groups) for c in imp.feature]
    imp = imp.sort_values("gain_share", ascending=False).reset_index(drop=True)

    # ---- stage 2: refit on train+validation ----
    trva = tr | va
    ens.fit_refit(X[trva], y_all[trva])
    p_te = ens.proba(X[te])
    stage2 = {"xgb_iter": ens.xgb_iter, "lgb_iter": ens.lgb_iter, "test": metrics(y_all[te], p_te, thr),
              "test_auc_ci95": bootstrap_auc(y_all[te], p_te),
              "test_at_0.5": metrics(y_all[te], p_te, 0.5)}
    log(f"stage2 test AUC={stage2['test']['auc']:.4f} CI95={stage2['test_auc_ci95']}")

    # robustness: AUC by carrier and month on test
    te_df = pd.DataFrame({"carrier": np.array(groups["categorical_codes"]["carrier_code"])[df.loc[te, "carrier_code"]],
                          "month": df.loc[te, "month"].astype(int).values, "y": y_all[te], "p": p_te})
    by_carrier = {k: float(roc_auc_score(g.y, g.p)) for k, g in te_df.groupby("carrier") if g.y.nunique() == 2}
    by_month = {int(k): float(roc_auc_score(g.y, g.p)) for k, g in te_df.groupby("month")}

    # ---- score every flight with the production model ----
    p_all = np.empty(len(df), dtype="float64")
    for s in range(0, len(df), 200_000):
        p_all[s:s + 200_000] = ens.proba(X.iloc[s:s + 200_000])
    split = np.where(dk >= TEST_START, "test", np.where(dk >= VAL_START, "validation", "train"))
    dist = df["distance_miles"].astype(float).to_numpy()
    hub = df["origin_hub_flights"].astype(float).to_numpy()
    impact = 0.5 * (dist - dist.min()) / (dist.max() - dist.min()) + 0.5 * (hub - hub.min()) / (hub.max() - hub.min())
    high_risk, high_impact = p_all >= thr, impact >= np.median(impact)
    tier = np.select([high_risk & high_impact, high_risk & ~high_impact, ~high_risk & high_impact],
                     ["P1", "P2", "P3"], "P4")
    cats = groups["categorical_codes"]
    out = pd.DataFrame({
        "FlightKey": df["flight_key"].astype("int64"), "DateKey": df["date_key"].astype("int32"),
        "CarrierKey": df["carrier_key"].astype("int32"), "OriginAirportKey": df["origin_airport_key"].astype("int32"),
        "DestAirportKey": df["dest_airport_key"].astype("int32"), "RouteKey": df["route_key"].astype("int32"),
        "FlightNumber": df["flight_number"].astype("int32"),
        "FlightDate": pd.to_datetime(df["date_key"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d"),
        "CRSDepTime": df["scheduled_dep_time"].astype("int32"),
        "ScheduledDepHour": df["scheduled_dep_hour"].astype("int32"),
        "DistanceMiles": df["distance_miles"].astype("float32"),
        "OriginCode": np.array(cats["origin"])[df["origin"]], "DestCode": np.array(cats["dest"])[df["dest"]],
        "CarrierCode": np.array(cats["carrier_code"])[df["carrier_code"]],
        "Delay_Probability": np.round(p_all, 4),
        "Predicted_Delayed15": high_risk.astype("int8"),
        "Risk_Level": np.select([p_all >= 0.5, p_all >= thr], ["Cao", "Trung bình"], "Thấp"),
        "Impact_Score": np.round(impact, 4),
        "Priority_Score": np.round(100 * p_all * (0.5 + 0.5 * impact), 2),
        "Priority_Tier": tier,
        "Actual_Delayed15": pd.array(np.where(lab, y_all, np.nan), dtype="Int8"),
        "DatasetSplit": split, "IsLabelled": lab.astype("int8")})
    out["RouteName"] = out.OriginCode + "-" + out.DestCode
    csv_path = os.path.join(OUT, "DSS_Flight_Prediction.csv")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    out.head(300).to_csv(os.path.join(OUT, "DSS_Flight_Prediction_sample.csv"), index=False, encoding="utf-8-sig")
    tiers = pd.DataFrame({
        "Priority_Tier": ["P1", "P2", "P3", "P4"],
        "Tier_Name": ["Rủi ro cao · Tác động cao", "Rủi ro cao · Tác động thấp",
                      "Rủi ro thấp · Tác động cao", "Rủi ro thấp · Tác động thấp"],
        "Recommended_Action": ["Can thiệp ngay: bố trí tàu/tổ bay dự phòng, ưu tiên cổng, báo khách sớm",
                               "Theo dõi sát: cảnh báo OCC, chuẩn bị phương án đổi chuyến",
                               "Giám sát định kỳ: chuyến tác động lớn, rủi ro thấp",
                               "Vận hành bình thường"],
        "Sort_Order": [1, 2, 3, 4], "Decision_Threshold": [thr] * 4,
        "Prediction_Horizon": ["T-60 phút"] * 4, "Model_Version": ["v14-ensemble-xgb-lgb-isotonic"] * 4})
    tiers.to_csv(os.path.join(OUT, "DSS_Priority_Tier.csv"), index=False, encoding="utf-8-sig")
    log(f"wrote {csv_path} rows={len(out)} prob range=[{p_all.min():.4f},{p_all.max():.4f}]")

    # ---- artifacts ----
    mdir = os.path.join(OUT, "models"); os.makedirs(mdir, exist_ok=True)
    ens.xgb_model.save_model(os.path.join(mdir, "xgb_v14.json"))
    ens.lgb_model.save_model(os.path.join(mdir, "lgb_v14.txt"))
    joblib.dump(ens.calibrator, os.path.join(mdir, "isotonic_v14.joblib"))
    with open(os.path.join(mdir, "feature_spec_v14.json"), "w", encoding="utf-8") as fh:
        json.dump({"features": feats, "categorical": CATEGORICAL, "categorical_codes": cats,
                   "threshold": thr, "horizon_min": H, "xgb_params": ens.xgb_params,
                   "lgb_params": LGB_PARAMS}, fh, ensure_ascii=False, indent=1)
    imp.to_csv(os.path.join(OUT, "feature_importance_v14.csv"), index=False, encoding="utf-8-sig")
    ablation = [dict(label=k, **v) for k, v in _ablation_records().items()]
    ablation.append(dict(label="v14 Ensemble (stage 1)", val_auc=stage1["val"]["auc"], test_auc=stage1["test"]["auc"]))
    ablation.append(dict(label="v14 Ensemble refit", val_auc=stage1["val"]["auc"], test_auc=stage2["test"]["auc"]))
    plot_all(os.path.join(OUT, "figures"), y_all[va], p_va, y_all[te], p_te, thr, imp, ablation)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "runtime_sec": round(time.time() - t0, 1),
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "device": device,
                        "xgboost": xgb.__version__, "lightgbm": lgb.__version__, "sklearn": sklearn.__version__},
        "target": "is_arr_delayed15 = 1 (FAA: arrival delay >= 15 min), completed flights",
        "prediction_time": "T_pred = CRS departure - 60 min",
        "split": {"train": "2022-01-01..2023-04-30", "validation": "2023-05-01..2023-06-30",
                  "test": "2023-07-01..2023-08-31"},
        "rows": {"total": int(len(df)), "labelled": int(lab.sum()), "train": int(tr.sum()),
                 "validation": int(va.sum()), "test": int(te.sum())},
        "feature_count": len(feats),
        "feature_family_counts": imp.family.value_counts().to_dict(),
        "banned_post_event": ["dep_delay/arr_delay of the flight", "taxi_out", "taxi_in", "wheels_off/on",
                              "air_time", "actual elapsed_time", "cancelled/diverted of the flight",
                              "delay_due_* causes"],
        "stage1_selection": stage1, "stage2_production": stage2,
        "test_auc_by_carrier": by_carrier, "test_auc_by_month": by_month,
        "csv": {"path": os.path.basename(csv_path), "bytes": os.path.getsize(csv_path), "sha256": sha256(csv_path),
                "tier_counts": pd.Series(tier).value_counts().to_dict()},
    }
    with open(os.path.join(OUT, "model_summary_v14.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    log(f"DONE in {time.time() - t0:.0f}s")


def _rnd_path(name: str) -> str:
    """R&D evidence lives in WORK during experiments and in ../reports/rnd inside the repo."""
    for d in (WORK, os.path.join(HERE, "..", "reports", "rnd")):
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return os.path.join(WORK, name)


def _ablation_records() -> dict:
    recs = {}
    labels = {"A0_D-1_strict": "A0 D-1 CRS+lịch sử", "A1_D-1_weather": "A1 +thời tiết",
              "B1_T-60_aircraft": "B1 +tàu bay inbound", "B2_T-60_full": "B2 +nowcast sân bay"}
    p = _rnd_path("experiment_v11_ablation.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        for k, lab in labels.items():
            if k in d:
                recs[lab] = dict(val_auc=d[k]["val_auc"], test_auc=d[k]["test_auc"])
    p = _rnd_path("experiment_v13_hl180.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        recs["C1 +nowcast mạng lưới"] = dict(val_auc=d["C1_xgb"]["val"]["auc"], test_auc=d["C1_xgb"]["test"]["auc"])
    return recs


if __name__ == "__main__":
    main()
