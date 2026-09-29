"""Star-schema CSV export for the Power BI model described in powerbi/README.md."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def export(out: Path, inst: pd.DataFrame, res: pd.DataFrame, exceptions: pd.DataFrame,
           controls: pd.DataFrame, bias: pd.DataFrame) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    dates = pd.DataFrame({"asof": sorted(res["asof"].unique())})
    dates["month"] = dates["asof"].dt.strftime("%b %Y")
    dates["month_index"] = range(1, len(dates) + 1)
    dates["is_latest"] = dates.month_index == len(dates)

    fact = res[["asof", "position_id", "derived_level", "desk_mark", "consensus", "n_quotes", "variance_u",
                "tolerance_u", "tol_ratio", "pv_adjustment_usd", "ava_mpu_usd", "ava_coc_usd", "quote_age_bd",
                "mark_age_periods", "rule_flags", "anomaly_score", "anomaly_flag", "status", "severity"]].copy()
    fact["is_breach"] = fact.rule_flags.str.contains("BREACH|FAT_FINGER")

    tables = {
        "dim_position": inst.drop(columns=["unit_scale"]),
        "dim_date": dates,
        "fact_ipv": fact,
        "fact_exception": exceptions,
        "fact_control": controls,
        "fact_desk_bias": bias,
    }
    paths = []
    for name, df in tables.items():
        p = out / f"{name}.csv"
        df.to_csv(p, index=False, date_format="%Y-%m-%d")
        paths.append(p)
    return paths
