"""Unsupervised anomaly triage on top of the rule engine.

Rules answer "is this mark outside tolerance?". They miss combinations
that are individually unremarkable: a mark that stopped moving while its
market moved, a single wide quote on a position that normally has four,
a quote that is old but not yet past the stale cut-off. An Isolation Forest
over normalised features surfaces those for a human to look at. It never
raises an exception by itself; it only ranks a review queue.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .config import ASSET_CLASSES, RunConfig

FEATURES = ["log_tol_ratio", "dispersion_tol", "quote_gap", "quote_age_rel", "log_move_gap", "mark_age"]


def features(res: pd.DataFrame) -> pd.DataFrame:
    stale_bd = res.asset_class.map(lambda a: ASSET_CLASSES[a]["stale_bd"])
    # Typical quote count for this asset class and level, so "fewer than usual" is relative.
    usual_q = res.groupby(["asset_class", "booked_level"]).n_quotes.transform("median")
    f = pd.DataFrame(index=res.index)
    f["log_tol_ratio"] = np.log1p(res.tol_ratio.fillna(0))
    f["dispersion_tol"] = (res.dispersion_u / res.tolerance_u).fillna(0).clip(upper=10)
    f["quote_gap"] = (usual_q - res.n_quotes).clip(lower=0)
    f["quote_age_rel"] = (res.quote_age_bd.astype(float).fillna(0) / stale_bd).clip(upper=10)
    f["log_move_gap"] = np.log1p(res.mark_move_gap.abs().fillna(0))
    f["mark_age"] = res.mark_age_periods.fillna(0).clip(upper=6)
    return f[FEATURES]


def score(res: pd.DataFrame, cfg: RunConfig) -> pd.DataFrame:
    X = features(res)
    model = IsolationForest(n_estimators=300, contamination=cfg.anomaly_contamination,
                            random_state=cfg.seed)
    model.fit(X)
    out = res.copy()
    out["anomaly_score"] = -model.score_samples(X)  # higher = more unusual
    out["anomaly_flag"] = (model.predict(X) == -1).astype(int)
    # Crude attribution for the reviewer: which feature is most extreme vs its median.
    z = (X - X.median()) / (X.quantile(0.9) - X.quantile(0.1)).replace(0, 1)
    out["anomaly_driver"] = np.where(out.anomaly_flag == 1, z.idxmax(axis=1), "")
    return out
