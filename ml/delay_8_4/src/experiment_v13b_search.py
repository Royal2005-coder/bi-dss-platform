# -*- coding: utf-8 -*-
"""Experiment 13b: small random search for XGBoost GPU on the T-60 feature set (validation AUC only)."""
import json
import os

import numpy as np
import xgboost as xgb
from sklearn.metrics import roc_auc_score

import experiment_v11_ablation as v11
from experiment_v13_tuning import load_all

N_TRIALS = 14


def main() -> None:
    df, feats = load_all()
    lab = df["target"].notna()
    tr = lab & (df.date_key <= 20230430)
    va = lab & df.date_key.between(20230501, 20230630)
    y = df["target"].to_numpy()
    dtr = xgb.QuantileDMatrix(df.loc[tr, feats], y[tr], max_bin=256)
    dva = xgb.QuantileDMatrix(df.loc[va, feats], y[va], ref=dtr)
    rng = np.random.default_rng(v11.SEED)
    trials = []
    for i in range(N_TRIALS):
        p = dict(v11.PARAMS, eta=0.03,
                 max_depth=int(rng.choice([6, 7, 8, 9, 10])),
                 min_child_weight=float(rng.choice([5, 20, 50, 100, 200])),
                 colsample_bytree=float(rng.choice([0.3, 0.4, 0.5, 0.6, 0.8])),
                 subsample=float(rng.choice([0.7, 0.8, 0.9])),
                 reg_lambda=float(rng.choice([1, 5, 10, 30, 100])),
                 gamma=float(rng.choice([0, 0.5, 1, 2])))
        bst = xgb.train(p, dtr, 8000, evals=[(dva, "val")], early_stopping_rounds=250, verbose_eval=False)
        auc = float(roc_auc_score(y[va], bst.predict(dva, iteration_range=(0, bst.best_iteration + 1))))
        trials.append(dict(params={k: p[k] for k in ("max_depth", "min_child_weight", "colsample_bytree",
                                                     "subsample", "reg_lambda", "gamma")},
                           best_iter=int(bst.best_iteration), val_auc=auc))
        print(i, trials[-1], flush=True)
    trials.sort(key=lambda t: -t["val_auc"])
    with open(os.path.join(v11.WORK, "experiment_v13b_search.json"), "w", encoding="utf-8") as fh:
        json.dump(trials, fh, indent=2)
    print("BEST", trials[0], flush=True)


if __name__ == "__main__":
    main()
