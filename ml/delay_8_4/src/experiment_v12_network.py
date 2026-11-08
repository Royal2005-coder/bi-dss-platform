# -*- coding: utf-8 -*-
"""Experiment 12: B2 (T-60 full) vs B3 = B2 + network-state nowcast (UTC clock). Same learner as v11."""
import json
import os
import sys

import pandas as pd

import experiment_v11_ablation as v11

H = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def main() -> None:
    df, now_cols = v11.load(H)
    net = pd.read_parquet(os.path.join(v11.WORK, f"network_h{H}.parquet"))
    net_cols = [c for c in net.columns if c != "flight_key"]
    df = df.merge(net.astype({c: "float32" for c in net_cols}), on="flight_key", validate="one_to_one")
    lab = df["target"].notna()
    masks = (lab & (df.date_key <= 20230430), lab & df.date_key.between(20230501, 20230630),
             lab & (df.date_key >= 20230701))
    b2 = [c for c in df.columns if c not in v11.NON_FEATURES and c not in net_cols]
    out = {}
    for name, feats in [(f"B3_T-{H}_network", b2 + net_cols), (f"N_T-{H}_network_only_on_A1", 
                         [c for c in b2 if c not in now_cols] + net_cols)]:
        res, imp = v11.fit_eval(df, feats, masks)
        out[name] = res
        print(name, res, flush=True)
        print(imp.head(25).to_string(), flush=True)
        imp.to_csv(os.path.join(v11.WORK, f"importance_v12_{name}.csv"))
    json.dump(out, open(os.path.join(v11.WORK, f"experiment_v12_network_h{H}.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
