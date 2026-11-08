# -*- coding: utf-8 -*-
"""DSS decision layer (8.4, v15): turns calibrated probabilities into auditable OCC decisions.

Follows the decision-logic principles: eliminate before optimising (hard-stop rules), flag risk instead of
hiding it (conformal uncertainty), never auto-decide (every output is a recommendation for the OCC duty
officer), and justify (reason codes from SHAP grouped by feature family).

All parameters (cost ratio, alpha, rules, capacity) are fitted or chosen on VALIDATION and then applied
unchanged to TEST.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

# Cost of a needless intervention vs cost of an unmitigated >=15-min delay (relative units).
# Literature on airline delay cost (EUROCONTROL/FAA ~ USD 75-100 per delay-minute, ~40 min avg tail of delayed
# flights) vs a precautionary action (spare crew call-out, gate swap, early pax notice) -> ratio ~1:4.
DEFAULT_COST_FP, DEFAULT_COST_FN = 1.0, 4.0


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    column: str
    op: str
    value: float


@dataclass
class PolicyFit:
    threshold: float
    conformal_q0: float
    conformal_q1: float
    alpha: float
    rules: list[Rule] = field(default_factory=list)
    rule_stats: dict = field(default_factory=dict)
    capacity_share: float = 0.10


CANDIDATE_RULES = [
    Rule("R1_inbound_late", "Chặng inbound đã trễ vượt thời gian quay đầu kế hoạch", "turn_slack_lb", "<", -15.0),
    Rule("R2_inbound_cancel", "Chặng inbound đã bị hủy trước T-60", "in_cancel_known", ">=", 1.0),
    Rule("R3_inbound_delay60", "Chặng inbound đã trễ ≥ 60 phút", "in_delay_lb", ">=", 60.0),
    Rule("R4_carrier_meltdown", "Hãng có ≥ 60% chuyến cất cánh trễ trong 120 phút qua", "car_dep_r15_120", ">=", 0.60),
    Rule("R5_origin_backlog", "Sân bay đi tồn ≥ 8 chuyến quá giờ chưa cất cánh", "o_backlog", ">=", 8.0),
    Rule("R6_origin_thunder", "Dông tại sân bay đi trong 6h qua và tỷ lệ trễ hãng ≥ 40%", "o_wxh_thunder_6h&car_dep_r15_120",
         "combo", 0.40),
]


def _rule_mask(rule: Rule, df: pd.DataFrame) -> np.ndarray:
    if rule.op == "combo":
        a, b = rule.column.split("&")
        if a not in df or b not in df:
            return np.zeros(len(df), bool)
        return (df[a].fillna(0).to_numpy() >= 1) & (df[b].fillna(0).to_numpy() >= rule.value)
    if rule.column not in df:
        return np.zeros(len(df), bool)
    v = df[rule.column].to_numpy(dtype=float)
    return np.nan_to_num(v < rule.value if rule.op == "<" else v >= rule.value, nan=False).astype(bool) & ~np.isnan(v)


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    ph = k / n
    return float((ph + z * z / (2 * n) - z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def fit_policy(p_val: np.ndarray, y_val: np.ndarray, feats_val: pd.DataFrame, alpha: float = 0.10,
               cost_fp: float = DEFAULT_COST_FP, cost_fn: float = DEFAULT_COST_FN, min_rule_precision: float = 0.80,
               min_rule_support: int = 30, capacity_share: float = 0.10) -> PolicyFit:
    # Bayes-optimal threshold for calibrated probabilities: act iff p * C_fn > (1 - p) * C_fp
    thr = cost_fp / (cost_fp + cost_fn)
    # Mondrian (class-conditional) split conformal: nonconformity = 1 - p(true class)
    s1 = 1 - p_val[y_val == 1]
    s0 = p_val[y_val == 0]
    q1 = float(np.quantile(s1, np.ceil((len(s1) + 1) * (1 - alpha)) / len(s1), method="higher"))
    q0 = float(np.quantile(s0, np.ceil((len(s0) + 1) * (1 - alpha)) / len(s0), method="higher"))
    rules, stats = [], {}
    for r in CANDIDATE_RULES:
        m = _rule_mask(r, feats_val)
        n = int(m.sum())
        prec = float(y_val[m].mean()) if n else float("nan")
        lb = wilson_lower(int(y_val[m].sum()), n)
        stats[r.name] = dict(description=r.description, val_support=n, val_precision=prec, val_wilson_lb95=lb,
                             accepted=bool(n >= min_rule_support and lb >= min_rule_precision))
        if stats[r.name]["accepted"]:
            rules.append(r)
    return PolicyFit(thr, q0, q1, alpha, rules, stats, capacity_share)


def apply_policy(fit: PolicyFit, p: np.ndarray, feats: pd.DataFrame, impact: np.ndarray,
                 slot: np.ndarray) -> pd.DataFrame:
    """slot = operational capacity bucket (origin airport x scheduled hour x date)."""
    contains1 = (1 - p) <= fit.conformal_q1
    contains0 = p <= fit.conformal_q0
    conf_set = np.select([contains1 & ~contains0, contains0 & ~contains1, contains0 & contains1],
                         ["{Trễ}", "{Đúng giờ}", "{Đúng giờ, Trễ}"], "{}")
    hard = np.zeros(len(p), bool)
    hard_reason = np.full(len(p), "", dtype=object)
    for r in fit.rules:
        m = _rule_mask(r, feats) & ~hard
        hard_reason[m] = r.description
        hard |= m
    score = p * (0.5 + 0.5 * impact)
    # capacity: top share of flights per slot by score (at least 1 per slot when anything is above threshold)
    s = pd.DataFrame({"slot": slot, "score": score})
    rank = s.groupby("slot")["score"].rank(ascending=False, method="first").to_numpy()
    size = s.groupby("slot")["score"].transform("size").to_numpy()
    in_capacity = rank <= np.maximum(1, np.ceil(fit.capacity_share * size))
    act = p >= fit.threshold
    uncertain = conf_set == "{Đúng giờ, Trễ}"
    # uncertainty is reported as a confidence label for the duty officer; it never demotes a high-risk flight
    tier = np.select([hard, act & in_capacity, act & ~in_capacity, impact >= np.median(impact)],
                     ["P1", "P1", "P2", "P3"], "P4")
    decision = np.select([hard, tier == "P1", tier == "P2", tier == "P3"],
                         ["Hard-stop: can thiệp ngay", "Đề xuất can thiệp (trong năng lực OCC)",
                          "Rủi ro cao ngoài năng lực: theo dõi sát", "Giám sát định kỳ"], "Vận hành bình thường")
    return pd.DataFrame({"Hard_Stop": hard.astype("int8"), "Hard_Stop_Reason": hard_reason,
                         "Conformal_Set": conf_set, "Is_Uncertain": uncertain.astype("int8"),
                         "Confidence": np.where(hard, "Rất cao (luật vận hành)", np.where(uncertain, "Thấp – cần điều độ xác nhận", "Cao")),
                         "In_OCC_Capacity": in_capacity.astype("int8"), "Priority_Score": np.round(100 * score, 2),
                         "Priority_Tier": tier, "DSS_Decision": decision})


def evaluate_policy(fit: PolicyFit, out: pd.DataFrame, y: np.ndarray, p: np.ndarray,
                    cost_fp: float = DEFAULT_COST_FP, cost_fn: float = DEFAULT_COST_FN) -> dict:
    act = out.Priority_Tier.isin(["P1"]).to_numpy()
    flag = (p >= fit.threshold)
    cs = out.Conformal_Set.to_numpy()
    covered = np.where(y == 1, np.char.find(cs.astype(str), "Trễ") >= 0, np.char.find(cs.astype(str), "Đúng") >= 0)

    def cost(mask):
        return float(cost_fp * ((mask) & (y == 0)).sum() + cost_fn * ((~mask) & (y == 1)).sum())
    base_none = cost(np.zeros_like(flag))
    res = {
        "threshold_bayes": fit.threshold,
        "conformal": {"alpha": fit.alpha, "coverage": float(covered.mean()),
                      "coverage_delayed": float(covered[y == 1].mean()), "coverage_ontime": float(covered[y == 0].mean()),
                      "uncertain_share": float(out.Is_Uncertain.mean()),
                      "auc_on_confident": float(roc_auc_score(y[out.Is_Uncertain == 0], p[out.Is_Uncertain == 0]))
                      if (out.Is_Uncertain == 0).sum() > 100 and len(np.unique(y[out.Is_Uncertain == 0])) == 2 else None},
        "hard_stop": {"share": float(out.Hard_Stop.mean()),
                      "precision": float(y[out.Hard_Stop == 1].mean()) if out.Hard_Stop.sum() else None,
                      "rules": {k: v for k, v in fit.rule_stats.items()}},
        "p1_act": {"share": float(act.mean()), "precision": float(y[act].mean()) if act.any() else None,
                   "recall": float(act[y == 1].mean()), "lift": float(y[act].mean() / y.mean()) if act.any() else None},
        "cost_relative_to_no_model": {"no_action": 1.0, "threshold_only": cost(flag) / base_none,
                                      "dss_p1_capacity": cost(act) / base_none},
    }
    return res


def capacity_curve(p: np.ndarray, y: np.ndarray, impact: np.ndarray, slot: np.ndarray, threshold: float,
                   shares=(0.05, 0.10, 0.20, 0.30, 0.50, 1.0), cost_fp: float = DEFAULT_COST_FP,
                   cost_fn: float = DEFAULT_COST_FN) -> list[dict]:
    """Trade-off table for management: OCC capacity share per slot -> precision / recall / relative cost."""
    s = pd.DataFrame({"slot": slot, "score": p * (0.5 + 0.5 * impact)})
    rank = s.groupby("slot")["score"].rank(ascending=False, method="first").to_numpy()
    size = s.groupby("slot")["score"].transform("size").to_numpy()
    base = cost_fn * y.sum()
    rows = []
    for sh in shares:
        act = (p >= threshold) & (rank <= np.maximum(1, np.ceil(sh * size)))
        c = cost_fp * (act & (y == 0)).sum() + cost_fn * (~act & (y == 1)).sum()
        rows.append(dict(capacity_share=sh, flagged_share=float(act.mean()),
                         precision=float(y[act].mean()) if act.any() else None, recall=float(act[y == 1].mean()),
                         cost_vs_no_model=float(c / base)))
    return rows
