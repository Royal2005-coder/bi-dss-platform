# -*- coding: utf-8 -*-
"""8.4.2 v15 - strictly pre-event T-60 delay model with monthly walk-forward deployment + DSS decision layer.

Protocol (fixed before test is scored)
  Features : schedule + history <= D-1 (from 2019) + aircraft-history proxy + airport/network nowcast +
             HOURLY weather observed <= T_pred (no daily perfect-forecast weather any more).
  Stage 1  : learners fit on labelled rows <= 2023-04-30 (years BI_TRAIN_YEARS), early stopping on
             validation 2023-05..06; greedy ensemble selection and isotonic calibration on validation.
  Stage 2  : walk-forward. July model = refit on all labelled rows <= 2023-06-30, calibrated on
             validation out-of-sample scores; August model = refit <= 2023-07-31, calibrated on July
             out-of-sample scores. Test = July + August scored by their own month model.
  DSS      : Bayes cost threshold, Wilson-validated hard-stop rules, OCC capacity per carrier-hour,
             adaptive conformal sets (daily update with outcomes known by D-1), SHAP reason codes.
Env: BI_WORK_DIR, BI_OUT_DIR, BI_TRAIN_YEARS (default 2019,2021,2022,2023), BI_USE_CATBOOST (0/1).
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
import numpy as np
import pandas as pd
import sklearn
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

import dss_policy as dss
from features_matrix import CATEGORICAL, build_matrix
from train_final_v14 import bootstrap_auc, f1_threshold, metrics

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(HERE, "..", "work"))
OUT = os.environ.get("BI_OUT_DIR", os.path.join(HERE, "..", "outputs_v15"))
YEARS = tuple(int(y) for y in os.environ.get("BI_TRAIN_YEARS", "2019,2021,2022,2023").split(","))
USE_CAT = os.environ.get("BI_USE_CATBOOST", "1") == "1"
SEED = 42
VAL_START, JUL, AUG, TEST_END = 20230501, 20230701, 20230801, 20230831
REFIT_SCALE = 1.15
PROB_CLIP = (0.001, 0.999)
XGB_P = dict(objective="binary:logistic", eval_metric="auc", tree_method="hist", device="cuda", eta=0.03,
             max_depth=6, min_child_weight=100.0, colsample_bytree=0.4, subsample=0.7, reg_lambda=10.0,
             gamma=2.0, max_bin=256, seed=SEED)
LGB_P = dict(objective="binary", metric="auc", learning_rate=0.03, num_leaves=127, min_child_samples=200,
             feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1, lambda_l2=20.0, max_bin=255,
             verbosity=-1, seed=SEED, n_jobs=-1)
CAT_P = dict(loss_function="Logloss", eval_metric="AUC", task_type="GPU", learning_rate=0.08, depth=8,
             l2_leaf_reg=10, border_count=128, random_seed=SEED, verbose=0)
FAMILY_VI = {"network": "Trễ dây chuyền mạng lưới/hãng", "airport": "Ùn tắc tại sân bay",
             "aircraft": "Tàu bay chặng trước trễ", "weather": "Thời tiết xấu", "history": "Lịch sử trễ của tuyến/giờ",
             "schedule": "Đặc điểm lịch bay"}


def log(m: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def family(c: str) -> str:
    if c.startswith(("nat_", "car_", "reg_", "cr_")) and "wxh" not in c:
        return "network"
    if "wxh" in c:
        return "weather"
    if c in {"has_inbound", "sched_turn_min", "in_status_known", "in_dep_delay_known", "in_cancel_known",
             "in_delay_lb", "turn_slack_lb"}:
        return "aircraft"
    if c.startswith(("o_", "d_")):
        return "airport"
    if c.endswith(("_prev_day", "_7d", "_30d", "_prior")) or c.startswith(("fn_", "route_fn_")):
        return "history"
    return "schedule"


class Learner:
    def __init__(self, kind: str) -> None:
        self.kind, self.model, self.iters, self.device = kind, None, 0, "cpu"

    def fit(self, X, y, Xv=None, yv=None, iters: int | None = None) -> "Learner":
        if self.kind == "xgb":
            for device in ("cuda", "cpu"):  # fall back to CPU when the shared GPU runs out of memory
                try:
                    p = dict(XGB_P, device=device)
                    d = xgb.QuantileDMatrix(X, y, max_bin=256)
                    if iters is None:
                        dv = xgb.QuantileDMatrix(Xv, yv, ref=d)
                        self.model = xgb.train(p, d, 12000, evals=[(dv, "v")], early_stopping_rounds=300, verbose_eval=False)
                        self.iters = self.model.best_iteration + 1
                    else:
                        self.model, self.iters = xgb.train(p, d, iters), iters
                    self.device = device
                    break
                except xgb.core.XGBoostError as exc:
                    if device == "cpu" or "memory" not in str(exc).lower():
                        raise
                    log(f"xgb CUDA out of memory on {len(y)} rows -> retry on CPU")
                finally:
                    d = dv = None
            if self.device == "cuda":
                self.model.set_param({"device": "cuda"})
        elif self.kind == "lgb":
            d = lgb.Dataset(X, y, free_raw_data=True)
            if iters is None:
                dv = lgb.Dataset(Xv, yv, reference=d)
                self.model = lgb.train(LGB_P, d, 12000, valid_sets=[dv], callbacks=[lgb.early_stopping(300, verbose=False)])
                self.iters = self.model.best_iteration
            else:
                self.model, self.iters = lgb.train(LGB_P, d, iters), iters
        else:
            from catboost import CatBoostClassifier
            if iters is None:
                self.model = CatBoostClassifier(iterations=8000, od_type="Iter", od_wait=300, **CAT_P)
                self.model.fit(X, y, eval_set=(Xv, yv), use_best_model=True)
                self.iters = self.model.get_best_iteration() + 1
            else:
                self.model = CatBoostClassifier(iterations=iters, **CAT_P)
                self.model.fit(X, y)
                self.iters = iters
        return self

    def predict(self, X) -> np.ndarray:
        out = np.empty(len(X), dtype=np.float64)
        for s in range(0, len(X), 250_000):
            chunk = X[s:s + 250_000]
            if self.kind == "xgb":
                out[s:s + len(chunk)] = self.model.predict(xgb.DMatrix(chunk), iteration_range=(0, self.iters))
            elif self.kind == "lgb":
                out[s:s + len(chunk)] = self.model.predict(chunk, num_iteration=self.iters)
            else:
                out[s:s + len(chunk)] = self.model.predict_proba(chunk)[:, 1]
        return out


def greedy_ensemble(preds: dict[str, np.ndarray], y: np.ndarray) -> list[str]:
    order = sorted(preds, key=lambda k: -roc_auc_score(y, preds[k]))
    chosen, best = [order[0]], roc_auc_score(y, preds[order[0]])
    for k in order[1:]:
        auc = roc_auc_score(y, np.mean([preds[c] for c in chosen + [k]], axis=0))
        if auc > best + 1e-4:
            chosen, best = chosen + [k], auc
    return chosen


def reason_codes(model: xgb.Booster, X: np.ndarray, cols: list[str]) -> tuple[np.ndarray, np.ndarray]:
    fams = np.array([family(c) for c in cols] + ["bias"])
    names = [f for f in FAMILY_VI]
    idx = {f: np.flatnonzero(fams == f) for f in names}
    r1 = np.empty(len(X), dtype=object)
    r2 = np.empty(len(X), dtype=object)
    for s in range(0, len(X), 100_000):
        c = model.predict(xgb.DMatrix(X[s:s + 100_000]), pred_contribs=True)
        g = np.stack([c[:, idx[f]].sum(axis=1) for f in names], axis=1)
        top = np.argsort(-g, axis=1)[:, :2]
        pos = np.take_along_axis(g, top, axis=1) > 0
        lab = np.array([FAMILY_VI[f] for f in names], dtype=object)
        r1[s:s + len(c)] = np.where(pos[:, 0], lab[top[:, 0]], "Không có yếu tố rủi ro nổi bật")
        r2[s:s + len(c)] = np.where(pos[:, 1], lab[top[:, 1]], "")
    return r1, r2


def aci_sets(p: np.ndarray, y: np.ndarray, day: np.ndarray, s0: np.ndarray, s1: np.ndarray,
             alpha: float = 0.10, gamma: float = 0.05) -> tuple[np.ndarray, list[float]]:
    """Adaptive conformal inference (Gibbs & Candes 2021) with class-conditional calibration scores.
    alpha_t is updated after each day with the miscoverage of that day (outcomes known by D-1)."""
    a_t, path = alpha, []
    sets = np.empty(len(p), dtype=object)
    for d in np.unique(day):
        m = day == d
        a = float(np.clip(a_t, 0.005, 0.5))
        q1, q0 = np.quantile(s1, 1 - a), np.quantile(s0, 1 - a)
        c1, c0 = (1 - p[m]) <= q1, p[m] <= q0
        sets[m] = np.select([c1 & ~c0, c0 & ~c1, c0 & c1], ["{Trễ}", "{Đúng giờ}", "{Đúng giờ, Trễ}"], "{}")
        err = float(np.mean(np.where(y[m] == 1, ~c1, ~c0)))
        a_t = a_t + gamma * (alpha - err)
        path.append(a)
    return sets, path


def main() -> None:
    t0 = time.time()
    os.makedirs(os.path.join(OUT, "models"), exist_ok=True)
    M = build_matrix(YEARS)
    X, y, dk = M.X, M.y, M.dk
    n_tr, n_va, n_jul, n_aug = (int(np.searchsorted(dk, d)) for d in (VAL_START, JUL, AUG, TEST_END + 1))
    log(f"years={YEARS} labelled={len(y)} train={n_tr} val={n_va - n_tr} jul={n_jul - n_va} aug={n_aug - n_jul} "
        f"unlabelled_scored={len(M.X_unl)} features={len(M.cols)} matrix={X.nbytes / 1e9:.1f}GB")
    kinds = ["xgb", "lgb"] + (["cat"] if USE_CAT else [])
    Xtr, ytr, Xva, yva = X[:n_tr], y[:n_tr], X[n_tr:n_va], y[n_tr:n_va]
    Xte, yte = X[n_va:n_aug], y[n_va:n_aug]

    # ---------------- stage 1 ----------------
    ckdir = os.path.join(OUT, "checkpoints")
    os.makedirs(ckdir, exist_ok=True)
    s1, pv, pt_static = {}, {}, {}
    for k in kinds:
        ck = os.path.join(ckdir, f"stage1_{k}.joblib")
        if os.path.exists(ck):
            s1[k], pv[k], pt_static[k] = joblib.load(ck)
            log(f"stage1 {k}: resumed from checkpoint")
            continue
        try:
            L = Learner(k).fit(Xtr, ytr, Xva, yva)
        except Exception as exc:  # noqa: BLE001 - optional learner (e.g. CatBoost GPU OOM) must not kill the run
            log(f"learner {k} skipped: {exc}")
            continue
        s1[k] = L
        pv[k], pt_static[k] = L.predict(Xva), L.predict(Xte)
        joblib.dump((L, pv[k], pt_static[k]), ck)
        log(f"stage1 {k}: iters={L.iters} val AUC={roc_auc_score(yva, pv[k]):.4f} "
            f"static test AUC={roc_auc_score(yte, pt_static[k]):.4f}")
    members = greedy_ensemble(pv, yva)
    sv = np.mean([pv[k] for k in members], axis=0)
    iso_val = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(sv, yva)
    p_va = np.clip(iso_val.predict(sv), *PROB_CLIP)
    p_te_static = np.clip(iso_val.predict(np.mean([pt_static[k] for k in members], axis=0)), *PROB_CLIP)
    thr_bayes = dss.DEFAULT_COST_FP / (dss.DEFAULT_COST_FP + dss.DEFAULT_COST_FN)
    thr_f1 = f1_threshold(yva, p_va)
    log(f"members={members} val AUC={roc_auc_score(yva, p_va):.4f} static test AUC={roc_auc_score(yte, p_te_static):.4f}")

    # ---------------- stage 2: monthly walk-forward ----------------
    p_month, raw_month, models_month = {}, {}, {}
    calib = iso_val
    for name, lo, hi in (("jul", n_va, n_jul), ("aug", n_jul, n_aug)):
        ck = os.path.join(ckdir, f"walkforward_{name}.joblib")
        if os.path.exists(ck):
            Ls, raw = joblib.load(ck)
            log(f"walk-forward {name}: resumed from checkpoint")
        else:
            Ls = []
            for k in members:
                ckm = os.path.join(ckdir, f"walkforward_{name}_{k}.joblib")
                if os.path.exists(ckm):
                    Ls.append(joblib.load(ckm))
                else:
                    Ls.append(Learner(k).fit(X[:lo], y[:lo], iters=int(s1[k].iters * REFIT_SCALE)))
                    joblib.dump(Ls[-1], ckm)
                    log(f"  walk-forward {name}: {k} refit done")
            raw = np.mean([L.predict(X[lo:hi]) for L in Ls], axis=0)
            joblib.dump((Ls, raw), ck)
        p_month[name] = np.clip(calib.predict(raw), *PROB_CLIP)
        raw_month[name], models_month[name] = raw, Ls
        log(f"walk-forward {name}: train={lo} AUC={roc_auc_score(y[lo:hi], raw):.4f}")
        calib = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw, y[lo:hi])  # for next month
    p_te = np.concatenate([p_month["jul"], p_month["aug"]])
    stage2 = {"test": metrics(yte, p_te, thr_bayes), "test_at_f1_threshold": metrics(yte, p_te, thr_f1),
              "test_auc_ci95": bootstrap_auc(yte, p_te),
              "test_auc_jul": float(roc_auc_score(y[n_va:n_jul], p_month["jul"])),
              "test_auc_aug": float(roc_auc_score(y[n_jul:n_aug], p_month["aug"]))}
    static = {"test": metrics(yte, p_te_static, thr_bayes), "test_auc_jul": float(roc_auc_score(y[n_va:n_jul], p_te_static[:n_jul - n_va])),
              "test_auc_aug": float(roc_auc_score(y[n_jul:n_aug], p_te_static[n_jul - n_va:]))}
    log(f"TEST walk-forward AUC={stage2['test']['auc']:.4f} CI95={stage2['test_auc_ci95']} "
        f"(jul {stage2['test_auc_jul']:.4f}, aug {stage2['test_auc_aug']:.4f}); static {static['test']['auc']:.4f}")

    # ---------------- DSS decision layer ----------------
    ci = {c: i for i, c in enumerate(M.cols)}
    rule_cols = ["turn_slack_lb", "in_cancel_known", "in_delay_lb", "car_dep_r15_120", "o_backlog", "o_wxh_thunder_6h"]
    fdf = lambda A: pd.DataFrame({c: A[:, ci[c]] for c in rule_cols if c in ci})  # noqa: E731
    fit = dss.fit_policy(p_va, yva.astype(int), fdf(Xva))
    s0_cal, s1_cal = p_va[yva == 0], 1 - p_va[yva == 1]
    aci, alpha_path = aci_sets(p_te, yte.astype(int), dk[n_va:n_aug], s0_cal, s1_cal)
    dist_all = np.concatenate([M.meta.distance_miles.to_numpy(float), M.meta_unl.distance_miles.to_numpy(float)])
    hub_all = np.concatenate([X[:, ci["origin_hub_flights"]], M.X_unl[:, ci["origin_hub_flights"]]]).astype(float)
    nz = lambda v: (v - np.nanmin(v)) / (np.nanmax(v) - np.nanmin(v))  # noqa: E731
    impact_all = 0.5 * nz(dist_all) + 0.5 * nz(hub_all)

    # ---------------- score every 2022-2023 flight ----------------
    i22 = int(np.searchsorted(dk, 20220101))  # labelled rows are date-sorted -> 2022+ is a contiguous slice
    keep = slice(i22, len(y))
    final = models_month["aug"]
    p_lab = np.full(len(y), np.nan)
    src = np.full(len(y), "", dtype=object)
    tr22 = slice(i22, n_tr)
    p_lab[tr22] = np.clip(calib.predict(np.mean([L.predict(X[tr22]) for L in final], axis=0)), *PROB_CLIP)
    src[tr22] = "in-sample (model ≤ 31/07/2023)"
    p_lab[n_tr:n_va], src[n_tr:n_va] = p_va, "out-of-sample (stage 1)"
    p_lab[n_va:n_jul], src[n_va:n_jul] = p_month["jul"], "out-of-sample (walk-forward 07/2023)"
    p_lab[n_jul:n_aug], src[n_jul:n_aug] = p_month["aug"], "out-of-sample (walk-forward 08/2023)"
    dku = M.dk_unl
    p_unl = np.empty(len(dku))
    groups = [(dku < VAL_START, final, calib), ((dku >= VAL_START) & (dku < JUL), [s1[k] for k in members], iso_val),
              ((dku >= JUL) & (dku < AUG), models_month["jul"], iso_val), (dku >= AUG, models_month["aug"], calib)]
    for m, Ls, cal in groups:
        if m.any():
            p_unl[m] = np.clip(cal.predict(np.mean([L.predict(M.X_unl[m]) for L in Ls], axis=0)), *PROB_CLIP)
    P = np.concatenate([p_lab[keep], p_unl])
    XX_keys = np.concatenate([M.keys[keep], M.keys_unl])
    DK = np.concatenate([dk[keep], dku])
    meta = pd.concat([M.meta.iloc[keep], M.meta_unl], ignore_index=True)
    feats_all = pd.concat([fdf(X[keep]), fdf(M.X_unl)], ignore_index=True)
    impact = np.concatenate([impact_all[:len(y)][keep], impact_all[len(y):]])
    carrier = meta.carrier_code.astype(str).to_numpy()
    slot = carrier + "_" + DK.astype(str) + "_" + meta.scheduled_dep_hour.astype(int).astype(str).to_numpy()
    pol = dss.apply_policy(fit, P, feats_all, impact, slot)
    xgb_final = next((L.model for L in final if L.kind == "xgb"), s1["xgb"].model)
    ra, rb = reason_codes(xgb_final, X[keep], M.cols)
    ua, ub = reason_codes(xgb_final, M.X_unl, M.cols)
    r1, r2 = np.concatenate([ra, ua]), np.concatenate([rb, ub])
    yy = np.concatenate([y[keep], np.full(len(dku), np.nan)])
    split = np.where(DK >= JUL, "test", np.where(DK >= VAL_START, "validation", "train"))
    out = pd.DataFrame({
        "FlightKey": XX_keys.astype("int64"), "DateKey": DK.astype("int32"),
        "FlightDate": pd.to_datetime(DK.astype(str), format="%Y%m%d").strftime("%Y-%m-%d"),
        "CarrierCode": carrier, "FlightNumber": meta.flight_number.astype("int32").to_numpy(),
        "OriginCode": meta.origin.astype(str).to_numpy(), "DestCode": meta.dest.astype(str).to_numpy(),
        "CRSDepTime": meta.scheduled_dep_time.astype("int32").to_numpy(),
        "ScheduledDepHour": meta.scheduled_dep_hour.astype("int32").to_numpy(),
        "DistanceMiles": meta.distance_miles.astype("float32").to_numpy(),
        "Delay_Probability": np.round(P, 4), "Predicted_Delayed15": (P >= fit.threshold).astype("int8"),
        "Risk_Level": np.select([P >= 0.5, P >= fit.threshold], ["Cao", "Trung bình"], "Thấp"),
        "Impact_Score": np.round(impact, 4), **{c: pol[c].to_numpy() for c in pol.columns},
        "Reason_1": r1, "Reason_2": r2,
        "Actual_Delayed15": pd.array(np.where(np.isnan(yy), np.nan, yy), dtype="Int8"),
        "DatasetSplit": split, "IsLabelled": (~np.isnan(yy)).astype("int8"),
        "Prediction_Source": np.concatenate([src[keep], np.full(len(dku), "chuyến hủy/chuyển hướng (chỉ chấm điểm)")])})
    te_mask = (out.DatasetSplit == "test").to_numpy() & (out.IsLabelled == 1).to_numpy()
    # ACI sets replace the static conformal sets on test rows (aligned by FlightKey)
    aci_map = pd.Series(aci, index=M.keys[n_va:n_aug])
    out.loc[te_mask, "Conformal_Set"] = aci_map.reindex(out.loc[te_mask, "FlightKey"]).to_numpy()
    out.loc[te_mask, "Is_Uncertain"] = (out.loc[te_mask, "Conformal_Set"] == "{Đúng giờ, Trễ}").astype("int8").to_numpy()
    out.loc[te_mask & (out.Hard_Stop == 0).to_numpy(), "Confidence"] = np.where(
        out.loc[te_mask & (out.Hard_Stop == 0).to_numpy(), "Is_Uncertain"] == 1, "Thấp – cần điều độ xác nhận", "Cao")
    out = out.sort_values("FlightKey").reset_index(drop=True)
    csv = os.path.join(OUT, "DSS_Flight_Prediction.csv")
    out.to_csv(csv, index=False, encoding="utf-8-sig")
    out.head(300).to_csv(os.path.join(OUT, "DSS_Flight_Prediction_sample.csv"), index=False, encoding="utf-8-sig")

    te_out = out[(out.DatasetSplit == "test") & (out.IsLabelled == 1)]
    yt = te_out.Actual_Delayed15.astype(int).to_numpy()
    pol_eval = dss.evaluate_policy(fit, te_out, yt, te_out.Delay_Probability.to_numpy())
    pol_eval["capacity_curve"] = dss.capacity_curve(te_out.Delay_Probability.to_numpy(), yt, te_out.Impact_Score.to_numpy(),
                                                    (te_out.CarrierCode + "_" + te_out.DateKey.astype(str) + "_" +
                                                     te_out.ScheduledDepHour.astype(str)).to_numpy(), fit.threshold)
    static_sets = dss.apply_policy(fit, p_te, fdf(Xte), np.zeros(len(p_te)), np.zeros(len(p_te))).Conformal_Set.to_numpy()
    cov = lambda sets: float(np.mean(np.where(yte == 1, np.char.find(sets.astype(str), "Trễ") >= 0,  # noqa: E731
                                             np.char.find(sets.astype(str), "Đúng") >= 0)))
    pol_eval["conformal_static_coverage"] = cov(static_sets)
    pol_eval["conformal_aci_coverage"] = cov(aci)
    pol_eval["aci_alpha_path"] = [round(a, 4) for a in alpha_path]

    # ---------------- artifacts ----------------
    for name, Ls in models_month.items():
        for L in Ls:
            fn = os.path.join(OUT, "models", f"{L.kind}_v15_{name}")
            if L.kind == "xgb":
                L.model.save_model(fn + ".json")
            elif L.kind == "lgb":
                L.model.save_model(fn + ".txt")
            else:
                L.model.save_model(fn + ".cbm")
    joblib.dump({"iso_val": iso_val, "iso_after_jul": calib}, os.path.join(OUT, "models", "calibrators_v15.joblib"))
    with open(os.path.join(OUT, "models", "feature_spec_v15.json"), "w", encoding="utf-8") as fh:
        json.dump({"features": M.cols, "categorical": CATEGORICAL, "categorical_codes": M.categories,
                   "members": members, "threshold_bayes": fit.threshold, "threshold_f1": thr_f1,
                   "train_years": YEARS, "horizon_min": 60, "xgb_params": XGB_P, "lgb_params": LGB_P,
                   "cat_params": CAT_P}, fh, ensure_ascii=False, indent=1)
    imp = pd.Series(s1["xgb"].model.get_score(importance_type="gain"))
    imp.index = [M.cols[int(k[1:])] if k.startswith("f") and k[1:].isdigit() else k for k in imp.index]
    imp = (imp / imp.sum()).rename("gain_share").reset_index().rename(columns={"index": "feature"})
    imp["family"] = imp.feature.map(family)
    imp.sort_values("gain_share", ascending=False).to_csv(os.path.join(OUT, "feature_importance_v15.csv"), index=False)
    h = hashlib.sha256(open(csv, "rb").read()).hexdigest()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "runtime_sec": round(time.time() - t0, 1),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "xgboost": xgb.__version__, "lightgbm": lgb.__version__, "sklearn": sklearn.__version__},
        "protocol": {"prediction_time": "CRS_DEP - 60 min", "train_years": YEARS, "weather": "hourly observed <= T_pred - 60 min",
                     "validation": "2023-05-01..2023-06-30", "test": "2023-07-01..2023-08-31 (monthly walk-forward)"},
        "rows": {"labelled_used": int(len(y)), "train": n_tr, "validation": n_va - n_tr, "test": n_aug - n_va,
                 "scored_2022_2023": int(len(out))},
        "stage1": {"members": members, "iters": {k: int(L.iters) for k, L in s1.items()},
                   "val_auc_by_learner": {k: float(roc_auc_score(yva, v)) for k, v in pv.items()},
                   "static_test_auc_by_learner": {k: float(roc_auc_score(yte, v)) for k, v in pt_static.items()},
                   "val": metrics(yva, p_va, thr_bayes), "val_at_f1_threshold": metrics(yva, p_va, thr_f1)},
        "static_test": static, "walk_forward_test": stage2, "dss_policy_test": pol_eval,
        "thresholds": {"bayes_cost_1_to_4": thr_bayes, "f1_on_validation": thr_f1},
        "csv": {"path": "DSS_Flight_Prediction.csv", "bytes": os.path.getsize(csv), "sha256": h,
                "tier_counts": out.Priority_Tier.value_counts().to_dict()},
    }
    with open(os.path.join(OUT, "model_summary_v15.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=float)
    log(f"DONE in {time.time() - t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
