"""Measure detection against the injected ground truth.

The engine never sees these labels. Scoring against them turns "the checks
work" into numbers: which issue types each layer catches, and what it costs
in false positives on clean positions.
"""
from __future__ import annotations

import pandas as pd

# Issue types that are position-level and so scorable against ipv_result rows.
POSITION_ISSUES = ["off_market_mark", "fat_finger", "stale_mark", "stale_quote", "no_quotes", "desk_bias"]


def detection_matrix(res: pd.DataFrame, bias: pd.DataFrame, controls: pd.DataFrame,
                     truth: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    r = res[["asof", "position_id", "desk", "asset_class", "status", "anomaly_flag"]].copy()
    flagged_blocks = bias.loc[bias.flagged == 1, ["asof", "desk", "asset_class"]].assign(bias_hit=1)
    r = r.merge(flagged_blocks, on=["asof", "desk", "asset_class"], how="left")
    r["bias_hit"] = r.bias_hit.fillna(0).astype(int)
    r["rules"] = (r.status == "EXCEPTION").astype(int)
    r["any_layer"] = r[["rules", "anomaly_flag", "bias_hit"]].max(axis=1)

    t = truth[truth.issue.isin(POSITION_ISSUES)].merge(r, on=["asof", "position_id"], how="inner")
    rows = []
    for issue, g in t.groupby("issue"):
        rows.append(dict(issue=issue, injected=len(g),
                         rules=g.rules.mean(), ml_triage=g.anomaly_flag.mean(),
                         desk_bias_test=g.bias_hit.mean(), any_layer=g.any_layer.mean()))
    matrix = pd.DataFrame(rows).set_index("issue").loc[lambda d: d.index.isin(POSITION_ISSUES)]

    dirty = truth[["asof", "position_id"]].drop_duplicates().assign(dirty=1)
    clean = r.merge(dirty, on=["asof", "position_id"], how="left").query("dirty != 1")
    dropped = truth[truth.issue == "dropped_in_feed"]
    recon_fail = controls[(controls.control == "RECON_COUNT") & (controls.status == "FAIL")]
    summary = {
        "clean_position_months": int(len(clean)),
        "rule_false_positive_rate": float(clean.rules.mean()),
        "ml_review_rate_on_clean": float(clean.anomaly_flag.mean()),
        "dropped_records_injected": int(len(dropped)),
        "dropped_records_caught_by_recon": int(dropped["asof"].isin(recon_fail["asof"]).sum()),
    }
    return matrix, summary
