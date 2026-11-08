# -*- coding: utf-8 -*-
"""Memory-lean model matrix for v15: one float32 array, labelled rows sorted by date (training sets are
prefix views), plus a small matrix of unlabelled 2022-2023 rows (cancelled/diverted) that are scored only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyarrow.dataset as pads
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("BI_WORK_DIR", os.path.join(HERE, "..", "work"))
CATEGORICAL = ["carrier_code", "origin", "dest", "origin_state", "dest_state", "origin_region", "dest_region",
               "season", "distance_group"]
DROP = {"flight_key", "date_key", "flight_date", "route", "arr_delay", "target", "is_cancelled", "is_diverted",
        "flight_number"}
PARTS = ("nowcast_h60", "network_h60_v2", "weather_h60")
HUB_WINDOW = (20220101, 20230430)


@dataclass
class Matrix:
    X: np.ndarray            # labelled rows, sorted by (date_key, flight_key)
    y: np.ndarray
    dk: np.ndarray
    keys: np.ndarray
    X_unl: np.ndarray        # unlabelled 2022-2023 rows (scored, never trained/evaluated)
    dk_unl: np.ndarray
    keys_unl: np.ndarray
    cols: list[str]
    categories: dict[str, list[str]]
    meta: pd.DataFrame       # labelled rows' carrier/origin/dest/hour/distance for reporting and slots
    meta_unl: pd.DataFrame


def _fill(table, cols, X, rows, cats, ci) -> None:
    for c in cols:
        a = table.column(c).to_pandas()
        if c in CATEGORICAL:
            a = pd.Categorical(a, categories=cats[c]).codes
        X[:, ci[c]] = np.asarray(a, dtype=np.float32)[rows]


def build_matrix(years: tuple[int, ...]) -> Matrix:
    path = os.path.join(WORK, "features_all.parquet")
    sel = pads.field("year").isin(list(years)) | (pads.field("date_key") >= 20220101)
    tbl = pq.read_table(path, filters=sel)
    cats = {c: sorted(map(str, pd.unique(tbl.column(c).to_pandas().astype(str)))) for c in CATEGORICAL}
    keys_all = tbl.column("flight_key").to_numpy()
    dk_all = tbl.column("date_key").to_numpy()
    y_all = tbl.column("target").to_pandas().to_numpy(dtype=np.float32)
    yr_all = tbl.column("year").to_numpy()
    lab = ~np.isnan(y_all) & np.isin(yr_all, years)
    unl = np.isnan(y_all) & (dk_all >= 20220101)
    lab_rows = np.flatnonzero(lab)[np.lexsort((keys_all[lab], dk_all[lab]))]
    unl_rows = np.flatnonzero(unl)
    base_cols = [c for c in tbl.column_names if c not in DROP]
    extra = {n: [c for c in pq.read_schema(os.path.join(WORK, f"{n}.parquet")).names if c != "flight_key"] for n in PARTS}
    cols = base_cols + ["origin_hub_flights", "dest_hub_flights"] + sum(extra.values(), [])
    ci = {c: i for i, c in enumerate(cols)}
    X = np.empty((len(lab_rows), len(cols)), dtype=np.float32)
    Xu = np.empty((len(unl_rows), len(cols)), dtype=np.float32)
    _fill(tbl, base_cols, X, lab_rows, cats, ci)
    _fill(tbl, base_cols, Xu, unl_rows, cats, ci)
    meta_cols = ["carrier_code", "origin", "dest", "scheduled_dep_hour", "scheduled_dep_time", "distance_miles",
                 "flight_number", "month"]
    meta_all = tbl.select(meta_cols).to_pandas()
    del tbl
    # hub size from the common training window only (no look-ahead)
    hub_mask = (dk_all[lab_rows] >= HUB_WINDOW[0]) & (dk_all[lab_rows] <= HUB_WINDOW[1])
    for side in ("origin", "dest"):
        codes = X[:, ci[side]].astype(int)
        cnt = np.bincount(codes[hub_mask], minlength=len(cats[side]))
        X[:, ci[f"{side}_hub_flights"]] = cnt[codes]
        Xu[:, ci[f"{side}_hub_flights"]] = cnt[Xu[:, ci[side]].astype(int)]
    for n, ccols in extra.items():
        t = pq.read_table(os.path.join(WORK, f"{n}.parquet"))
        k2 = t.column("flight_key").to_numpy()
        srt = np.argsort(k2)
        for rows, M in ((lab_rows, X), (unl_rows, Xu)):
            pos = srt[np.searchsorted(k2[srt], keys_all[rows])]
            assert (k2[pos] == keys_all[rows]).all(), n
            for c in ccols:
                M[:, ci[c]] = t.column(c).to_numpy(zero_copy_only=False).astype(np.float32)[pos]
        del t
    return Matrix(X, y_all[lab_rows], dk_all[lab_rows], keys_all[lab_rows], Xu, dk_all[unl_rows], keys_all[unl_rows],
                  cols, cats, meta_all.iloc[lab_rows].reset_index(drop=True),
                  meta_all.iloc[unl_rows].reset_index(drop=True))
