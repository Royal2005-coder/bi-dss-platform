# -*- coding: utf-8 -*-
"""v15 controlled experiment: honest hourly weather (H2) and multi-year training data (H1).

Same validation (2023-05..06) and test (2023-07..08), same XGBoost GPU recipe; selection on validation.
  E0  daily weather (v14, perfect-forecast proxy), train 2022-01..2023-04          (reference)
  E1  hourly weather <= T_pred (honest),           train 2022-01..2023-04
  E2  hourly weather,                              train 2019 + 2022-01..2023-04
  E3  hourly weather,                              train 2019 + 2021 + 2022-01..2023-04
Memory: one float32 matrix sorted by date; train/val/test are row views.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(HERE, "..", "work"))
DAILY_WX = os.environ.get("BI_WEATHER_PATH", os.path.join(HERE, "..", "data", "weather_daily.parquet"))
SEED = 42
CATEGORICAL = ["carrier_code", "origin", "dest", "origin_state", "dest_state", "origin_region", "dest_region",
               "season", "distance_group"]
DROP = {"flight_key", "date_key", "flight_date", "route", "arr_delay", "target", "is_cancelled", "is_diverted",
        "flight_number"}
TRAIN_END, VAL_START, VAL_END, TEST_START = 20230430, 20230501, 20230630, 20230701
PARAMS = dict(objective="binary:logistic", eval_metric="auc", tree_method="hist", device=os.environ.get("BI_XGB_DEVICE", "cuda"), eta=0.05,
              max_depth=6, min_child_weight=100.0, colsample_bytree=0.4, subsample=0.7, reg_lambda=10.0,
              gamma=2.0, max_bin=256, seed=SEED)


def load_matrix() -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray, np.ndarray]:
    """Labelled rows 2019, 2021-2023 sorted by date, all candidate features as one float32 matrix."""
    import pyarrow.dataset as pads
    import pyarrow.parquet as pq
    ROWS = pads.field("year").isin([2019, 2020, 2021, 2022, 2023]) & pads.field("target").is_valid()
    base_tbl = pq.read_table(os.path.join(WORK, "features_all.parquet"),
                             filters=ROWS)
    keys = base_tbl.column("flight_key").to_numpy()
    dk = base_tbl.column("date_key").to_numpy()
    order = np.lexsort((keys, dk))
    keys, dk = keys[order], dk[order]
    y = base_tbl.column("target").to_numpy()[order].astype(np.float32)
    yr = base_tbl.column("year").to_numpy()[order]
    base_cols = [c for c in base_tbl.column_names if c not in DROP]
    extra = {n: [c for c in pq.read_schema(os.path.join(WORK, f"{n}.parquet")).names if c != "flight_key"]
             for n in ("nowcast_h60", "network_h60_v2", "weather_h60")}
    wd = pd.read_parquet(DAILY_WX)
    wx_c = [c for c in wd.columns if c not in ("airport_code", "date_key")]
    daily = [f"{s}_wx_{c}" for s in ("origin", "dest") for c in wx_c] + ["origin_wx_missing", "dest_wx_missing",
             "wx_bad_hours_both", "wx_wind_max_both", "wx_precip_sum_both", "wx_bad_any"]
    cols = base_cols + ["origin_hub_flights", "dest_hub_flights"] + sum(extra.values(), []) + daily
    X = np.empty((len(keys), len(cols)), dtype=np.float32)
    ci = {c: k for k, c in enumerate(cols)}
    for c in base_cols:
        a = base_tbl.column(c)
        if c in CATEGORICAL:
            a = pd.Categorical(a.to_pandas()).codes
        X[:, ci[c]] = np.asarray(a.to_numpy(zero_copy_only=False) if hasattr(a, "to_numpy") else a, dtype=np.float32)[order]
    tr_mask = (dk >= 20220101) & (dk <= TRAIN_END)
    for side in ("origin", "dest"):
        codes = X[:, ci[side]]
        cnt = np.bincount(codes[tr_mask].astype(int), minlength=int(codes.max()) + 1)
        X[:, ci[f"{side}_hub_flights"]] = cnt[codes.astype(int)]
    del base_tbl
    for n, ccols in extra.items():
        t = pq.read_table(os.path.join(WORK, f"{n}.parquet"))
        k2 = t.column("flight_key").to_numpy()
        srt = np.argsort(k2)
        pos = srt[np.searchsorted(k2[srt], keys)]
        assert (k2[pos] == keys).all(), n
        for c in ccols:
            X[:, ci[c]] = t.column(c).to_numpy(zero_copy_only=False).astype(np.float32)[pos]
        del t
    # daily weather (v14 reference E0): join on (airport, date)
    ap_names = pd.Categorical(pq.read_table(os.path.join(WORK, "features_all.parquet"), columns=["origin"],
                                            filters=ROWS)
                              .column("origin").to_pandas()).categories
    for side in ("origin", "dest"):
        idx = pd.MultiIndex.from_arrays([wd.airport_code, wd.date_key])
        side_codes = X[:, ci[side]].astype(int)
        names = pd.Categorical(pq.read_table(os.path.join(WORK, "features_all.parquet"), columns=[side],
                                             filters=ROWS)
                               .column(side).to_pandas()).categories
        q = pd.MultiIndex.from_arrays([names[side_codes], dk])
        loc = idx.get_indexer(q)
        for c in wx_c:
            v = wd[c].to_numpy(np.float32)
            X[:, ci[f"{side}_wx_{c}"]] = np.where(loc >= 0, v[np.maximum(loc, 0)], np.nan)
        X[:, ci[f"{side}_wx_missing"]] = (loc < 0).astype(np.float32)
    del ap_names
    o, d = (lambda c: X[:, ci[f"origin_wx_{c}"]]), (lambda c: X[:, ci[f"dest_wx_{c}"]])
    X[:, ci["wx_bad_hours_both"]] = o("bad_hours") + d("bad_hours")
    X[:, ci["wx_wind_max_both"]] = np.fmax(o("wind_max"), d("wind_max"))
    X[:, ci["wx_precip_sum_both"]] = o("precip_sum") + d("precip_sum")
    X[:, ci["wx_bad_any"]] = ((o("bad_hours") > 0) | (d("bad_hours") > 0)).astype(np.float32)
    return X, cols, y, dk, yr


def main() -> None:
    t0 = time.time()
    variants = sys.argv[1:] or ["E0", "E1", "E2", "E3"]
    X, cols, y, dk, yr = load_matrix()
    daily = [c for c in cols if "_wx_" in c or c.startswith("wx_")]
    hourly = [c for c in cols if "wxh_" in c]
    base = [c for c in cols if c not in daily and c not in hourly]
    sets = {"E0": base + daily, "E1": base + hourly, "E2": base + hourly, "E3": base + hourly, "E4": base + hourly}
    scopes = {"E0": (2022,), "E1": (2022,), "E2": (2019, 2022), "E3": (2019, 2021, 2022), "E4": (2019, 2020, 2021, 2022)}
    va = (dk >= VAL_START) & (dk <= VAL_END)
    te = dk >= TEST_START
    print(f"matrix {X.shape} ({X.nbytes / 1e9:.1f} GB) in {time.time() - t0:.0f}s; base={len(base)} "
          f"daily={len(daily)} hourly={len(hourly)}", flush=True)
    out = {}
    for v in variants:
        idx = [cols.index(c) for c in sets[v]]
        tr = (dk <= TRAIN_END) & np.isin(np.where(yr == 2023, 2022, yr), scopes[v])
        dtr = xgb.QuantileDMatrix(X[np.ix_(np.flatnonzero(tr), idx)], y[tr], max_bin=256, feature_names=sets[v])
        dva = xgb.QuantileDMatrix(X[np.ix_(np.flatnonzero(va), idx)], y[va], ref=dtr, feature_names=sets[v])
        b = xgb.train(PARAMS, dtr, 8000, evals=[(dva, "val")], early_stopping_rounds=200, verbose_eval=False)
        it = (0, b.best_iteration + 1)
        pv = b.predict(dva, iteration_range=it)
        pt = b.predict(xgb.DMatrix(X[np.ix_(np.flatnonzero(te), idx)], feature_names=sets[v]), iteration_range=it)
        out[v] = dict(n_train=int(tr.sum()), n_feat=len(idx), best_iter=int(b.best_iteration),
                      val_auc=float(roc_auc_score(y[va], pv)), test_auc=float(roc_auc_score(y[te], pt)),
                      test_ap=float(average_precision_score(y[te], pt)))
        print(v, out[v], flush=True)
        pd.Series(b.get_score(importance_type="gain")).sort_values(ascending=False).to_csv(
            os.path.join(WORK, f"importance_v15_{v}.csv"))
        del dtr, dva, b
    with open(os.path.join(WORK, "experiment_v15.json"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(out) + chr(10))


if __name__ == "__main__":
    main()
