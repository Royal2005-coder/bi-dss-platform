# -*- coding: utf-8 -*-
"""Experiment 11 - controlled ablation by prediction horizon (R&D 8.4.2).

Same split, seed, learner (XGBoost GPU hist) and hyper-parameters; only the
feature set changes, so AUC differences are attributable to the feature group.

  A0  D-1 strict      : CRS + history <= D-1 (v8 minus same-day weather)
  A1  D-1 + weather   : A0 + daily weather of flight day (perfect-forecast proxy) = v8
  B1  T-H aircraft    : A1 + inbound leg of same carrier/flight number (aircraft-history proxy)
  B2  T-H full        : B1 + airport nowcast (origin/dest, only events known at T-H)

Usage: python experiment_v11_ablation.py [H ...]   (default 60 120)
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work")
SEED = 42
CATEGORICAL = ["carrier_code", "origin", "dest", "origin_state", "dest_state",
               "origin_region", "dest_region", "season", "distance_group"]
NON_FEATURES = {"flight_key", "date_key", "carrier_key", "origin_airport_key", "dest_airport_key",
                "route_key", "flight_number", "full_date", "flight_date_str", "arr_delay",
                "is_arr_delayed15", "is_completed", "is_cancelled", "is_diverted", "target",
                "carrier_name"}
INBOUND = ["has_inbound", "sched_turn_min", "in_status_known", "in_dep_delay_known",
           "in_cancel_known", "in_delay_lb", "turn_slack_lb"]
PARAMS = dict(objective="binary:logistic", eval_metric="auc", tree_method="hist", device="cuda",
              max_depth=8, eta=0.05, subsample=0.8, colsample_bytree=0.6, min_child_weight=20,
              reg_lambda=10.0, max_bin=256, seed=SEED)


def load(h):
    df = pd.read_parquet(os.path.join(WORK, "features_v8.parquet"))
    nc = pd.read_parquet(os.path.join(WORK, f"nowcast_h{h}.parquet"))
    nc = nc.astype({c: "float32" for c in nc.columns if c != "flight_key"})
    df = df.merge(nc, on="flight_key", how="left", validate="one_to_one")
    for c in CATEGORICAL:
        df[c] = df[c].astype("category").cat.codes.astype("int32")
    num = df.select_dtypes(include=["float64", "int64"]).columns.difference(["flight_key", "date_key"])
    df[num] = df[num].astype("float32")
    return df, [c for c in nc.columns if c != "flight_key"]


def feature_sets(df, now_cols):
    base = [c for c in df.columns if c not in NON_FEATURES and c not in now_cols]
    weather = [c for c in base if "_wx_" in c or c.startswith("wx_")]
    a0 = [c for c in base if c not in weather]
    airport_now = [c for c in now_cols if c not in INBOUND]
    return {"A0_D-1_strict": a0, "A1_D-1_weather": base,
            "B1_T-H_aircraft": base + INBOUND, "B2_T-H_full": base + INBOUND + airport_now}


def fit_eval(df, feats, masks):
    tr, va, te = masks
    y = df["target"].to_numpy()
    dtr = xgb.QuantileDMatrix(df.loc[tr, feats], y[tr])
    dva = xgb.QuantileDMatrix(df.loc[va, feats], y[va], ref=dtr)
    bst = xgb.train(PARAMS, dtr, 6000, evals=[(dva, "val")], early_stopping_rounds=200,
                    verbose_eval=False)
    it = (0, bst.best_iteration + 1)
    pv = bst.predict(xgb.DMatrix(df.loc[va, feats]), iteration_range=it)
    pt = bst.predict(xgb.DMatrix(df.loc[te, feats]), iteration_range=it)
    imp = pd.Series(bst.get_score(importance_type="gain")).sort_values(ascending=False)
    return dict(n_feat=len(feats), best_iter=int(bst.best_iteration),
                val_auc=float(roc_auc_score(y[va], pv)), test_auc=float(roc_auc_score(y[te], pt)),
                test_ap=float(average_precision_score(y[te], pt))), imp


def main():
    horizons = [int(a) for a in sys.argv[1:]] or [60, 120]
    out = {"learner": "XGBoost GPU hist", "params": PARAMS, "split": {
        "train": "<=2023-04-30", "val": "2023-05-01..2023-06-30", "test": ">=2023-07-01"}}
    for h in horizons:
        df, now_cols = load(h)
        lab = df["target"].notna()
        masks = (lab & (df.date_key <= 20230430), lab & df.date_key.between(20230501, 20230630),
                 lab & (df.date_key >= 20230701))
        sets = feature_sets(df, now_cols)
        if h != horizons[0]:  # D-1 sets do not depend on H
            sets = {k: v for k, v in sets.items() if k.startswith("B")}
        for name, feats in sets.items():
            t0 = time.time()
            res, imp = fit_eval(df, feats, masks)
            key = name.replace("T-H", f"T-{h}")
            res["sec"] = round(time.time() - t0, 1)
            out[key] = res
            print(key, res, flush=True)
            if name.startswith("B2"):
                print(imp.head(20).to_string(), flush=True)
                imp.to_csv(os.path.join(WORK, f"importance_v11_{key}.csv"))
        del df
    path = os.path.join(WORK, "experiment_v11_ablation.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print("wrote", path)


if __name__ == "__main__":
    main()
