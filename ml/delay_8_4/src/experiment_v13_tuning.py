# -*- coding: utf-8 -*-
"""Experiment 13: model-side improvements on the T-60 feature set (selection on VALIDATION only).

C1  B2 + network v2 features, XGBoost GPU (v11 params)
C2  C1 + recency sample weights (half-life HL days, counted back from end of train)
L2  LightGBM (CPU/GPU) on the C2 recipe
E2  rank-average ensemble XGB + LGBM
Test AUC is printed for the record but never used to choose.
"""
import json
import os
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import rankdata
from sklearn.metrics import average_precision_score, roc_auc_score

import experiment_v11_ablation as v11

H = 60
HL = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
LGB_PARAMS = dict(objective="binary", metric="auc", learning_rate=0.03, num_leaves=127,
                  min_child_samples=200, feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=20.0, max_bin=255, verbosity=-1, seed=v11.SEED, n_jobs=-1)


def load_all() -> tuple[pd.DataFrame, list[str]]:
    df, _ = v11.load(H)
    net = pd.read_parquet(os.path.join(v11.WORK, "network_h60_v2.parquet"))
    cols = [c for c in net.columns if c != "flight_key"]
    df = df.merge(net.astype({c: "float32" for c in cols}), on="flight_key", validate="one_to_one")
    feats = [c for c in df.columns if c not in v11.NON_FEATURES]
    return df, feats


def recency_weight(date_key: pd.Series) -> np.ndarray:
    d = pd.to_datetime(date_key.astype(str), format="%Y%m%d")
    age = (pd.Timestamp("2023-04-30") - d).dt.days.clip(lower=0).to_numpy()
    return np.power(0.5, age / HL).astype("float32")


def score(y: np.ndarray, p: np.ndarray) -> dict:
    return dict(auc=float(roc_auc_score(y, p)), ap=float(average_precision_score(y, p)))


def main() -> None:
    df, feats = load_all()
    lab = df["target"].notna()
    tr = lab & (df.date_key <= 20230430)
    va = lab & df.date_key.between(20230501, 20230630)
    te = lab & (df.date_key >= 20230701)
    y = df["target"].to_numpy()
    w = recency_weight(df.loc[tr, "date_key"])
    out, preds = {"n_feat": len(feats), "half_life_days": HL}, {}

    for name, weight in [("C1_xgb", None), ("C2_xgb_recency", w)]:
        dtr = xgb.QuantileDMatrix(df.loc[tr, feats], y[tr], weight=weight)
        dva = xgb.QuantileDMatrix(df.loc[va, feats], y[va], ref=dtr)
        bst = xgb.train(v11.PARAMS, dtr, 6000, evals=[(dva, "val")], early_stopping_rounds=200,
                        verbose_eval=False)
        it = (0, bst.best_iteration + 1)
        pv = bst.predict(xgb.DMatrix(df.loc[va, feats]), iteration_range=it)
        pt = bst.predict(xgb.DMatrix(df.loc[te, feats]), iteration_range=it)
        preds[name] = (pv, pt)
        out[name] = dict(best_iter=int(bst.best_iteration), val=score(y[va], pv), test=score(y[te], pt))
        print(name, out[name], flush=True)

    best_w = w if out["C2_xgb_recency"]["val"]["auc"] > out["C1_xgb"]["val"]["auc"] else None
    dtr = lgb.Dataset(df.loc[tr, feats], y[tr], weight=best_w, free_raw_data=False)
    dva = lgb.Dataset(df.loc[va, feats], y[va], reference=dtr)
    m = lgb.train(LGB_PARAMS, dtr, 8000, valid_sets=[dva],
                  callbacks=[lgb.early_stopping(250, verbose=False)])
    pv = m.predict(df.loc[va, feats], num_iteration=m.best_iteration)
    pt = m.predict(df.loc[te, feats], num_iteration=m.best_iteration)
    preds["L_lgb"] = (pv, pt)
    out["L_lgb"] = dict(best_iter=int(m.best_iteration), recency=best_w is not None,
                        val=score(y[va], pv), test=score(y[te], pt))
    print("L_lgb", out["L_lgb"], flush=True)

    xname = "C2_xgb_recency" if best_w is not None else "C1_xgb"
    ev = (rankdata(preds[xname][0]) + rankdata(preds["L_lgb"][0])) / 2
    et = (rankdata(preds[xname][1]) + rankdata(preds["L_lgb"][1])) / 2
    out["E_rank_avg"] = dict(members=[xname, "L_lgb"], val=score(y[va], ev), test=score(y[te], et))
    print("E_rank_avg", out["E_rank_avg"], flush=True)
    with open(os.path.join(v11.WORK, f"experiment_v13_hl{int(HL)}.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
