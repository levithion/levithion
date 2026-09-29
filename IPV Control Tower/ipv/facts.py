"""Month-end fact sheet: the single source of numbers for every report.

The Excel pack, the dashboard and the AI commentary all read from this one
dict, so a figure can never differ between two outputs of the same run.
Keys are flat strings so they can be cited as `{placeholders}`.
"""
from __future__ import annotations

import pandas as pd

from .config import RunConfig


def usd(x: float) -> str:
    a = abs(x)
    s = f"{a / 1e6:,.2f}m" if a >= 1e6 else f"{a / 1e3:,.0f}k" if a >= 1e3 else f"{a:,.0f}"
    return ("-" if x < 0 else "") + "USD " + s


def pct(x: float) -> str:
    return f"{x:.1%}"


def build(res: pd.DataFrame, bias: pd.DataFrame, controls: pd.DataFrame, cfg: RunConfig) -> dict:
    dates = sorted(res["asof"].unique())
    cur, prev = dates[-1], dates[-2]
    r, p = res[res["asof"] == cur], res[res["asof"] == prev]

    def breach_adj(x):
        return x.loc[x.rule_flags.str.contains("BREACH|FAT_FINGER"), "pv_adjustment_usd"].sum()

    def ava(x):
        return cfg.ava_diversification * (x.ava_mpu_usd.sum() + x.ava_coc_usd.sum())

    exc = r[r.status == "EXCEPTION"]
    lt = r[r.derived_level != r.booked_level]
    f: dict = {
        "asof": pd.Timestamp(cur).strftime("%d %b %Y"),
        "prior_asof": pd.Timestamp(prev).strftime("%d %b %Y"),
        "positions": f"{len(r):,}",
        "coverage_pct": pct((r.n_quotes > 0).mean()),
        "exceptions": f"{len(exc):,}",
        "exceptions_prior": f"{(p.status == 'EXCEPTION').sum():,}",
        "critical": f"{(exc.severity == 'CRITICAL').sum():,}",
        "high": f"{(exc.severity == 'HIGH').sum():,}",
        "breach_adj_usd": usd(breach_adj(r)),
        "breach_adj_prior_usd": usd(breach_adj(p)),
        "ava_usd": usd(ava(r)),
        "ava_prior_usd": usd(ava(p)),
        "level_downgrades": f"{(lt.derived_level > lt.booked_level).sum():,}",
        "level_upgrades": f"{(lt.derived_level < lt.booked_level).sum():,}",
        "l3_positions": f"{(r.derived_level == 3).sum():,}",
        "ml_review_queue": f"{((r.anomaly_flag == 1) & (r.status == 'PASS')).sum():,}",
        "control_failures": f"{(controls[controls['asof'] == cur].status == 'FAIL').sum():,}",
        "materiality_usd": usd(cfg.materiality_usd),
    }
    # Numeric twins for charts and tests (not exposed to the language model).
    f["_num"] = dict(breach_adj=breach_adj(r), breach_adj_prior=breach_adj(p), ava=ava(r),
                     exceptions=len(exc), positions=len(r))

    for desk, g in r.groupby("desk"):
        k = desk.lower().replace(" ", "_")
        f[f"{k}.exceptions"] = f"{(g.status == 'EXCEPTION').sum():,}"
        f[f"{k}.breach_adj_usd"] = usd(breach_adj(g))
        f[f"{k}.ava_usd"] = usd(ava(g))

    b = bias[(bias["asof"] == cur) & (bias.flagged == 1)]
    f["bias_blocks"] = f"{len(b):,}"
    for i, row in enumerate(b.itertuples(), 1):
        f[f"bias{i}.scope"] = f"{row.desk} / {row.asset_class.replace('_', ' ').lower()}"
        f[f"bias{i}.share_flattering"] = pct(row.share_same_sign)
        f[f"bias{i}.mean_tol"] = f"{row.mean_tol_ratio:.2f}x tolerance"

    # Top exceptions under local aliases: identifiers stay out of any prompt.
    top = exc.reindex(exc.pv_adjustment_usd.abs().sort_values(ascending=False).index).head(5)
    f["_aliases"] = {}
    for i, row in enumerate(top.itertuples(), 1):
        f["_aliases"][f"X{i}"] = row.position_id
        f[f"x{i}.desk"] = row.desk
        f[f"x{i}.asset_class"] = row.asset_class.replace("_", " ").lower()
        f[f"x{i}.flags"] = row.rule_flags.replace("|", ", ")
        f[f"x{i}.pv_usd"] = usd(row.pv_adjustment_usd) if pd.notna(row.pv_adjustment_usd) else "n/a"
        f[f"x{i}.tol_ratio"] = f"{row.tol_ratio:.1f}x" if pd.notna(row.tol_ratio) else "n/a"
    return f


def public(f: dict) -> dict:
    """The subset that may leave the machine: display strings only."""
    return {k: v for k, v in f.items() if not k.startswith("_")}
