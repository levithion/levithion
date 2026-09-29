"""Synthetic trading-book generator.

Real position and market data cannot be published, so this module builds a
book that behaves like one: instruments whose fair values random-walk across
month-ends, desk marks that sit close to fair value, and 1-4 independent
quotes per position whose count and spread depend on liquidity.

Known problems are injected on purpose (off-market marks, fat-fingers, marks
frozen for months, stale or missing quotes, a desk-level bias, a dropped
record in the feed). The labels are written to a separate ground-truth table
that the engine never reads; it exists only so detection rates can be
measured instead of asserted.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ASSET_CLASSES, DESKS, EXCHANGE_SOURCE, L1_ELIGIBLE, RunConfig

QUOTE_NOISE_TOL = 0.2  # quote noise s.d. as a fraction of the position's tolerance
SOURCES = ["Vendor composite", "Broker quote A", "Broker quote B", "Consensus service"]

# Natural level, monthly move, bid/ask and sensitivity range (in the tolerance unit) per asset class.
MARKET = {
    "GOVT_BOND": dict(level=(94, 106), move=0.9, half_spread=0.04, sens=(2_000, 60_000)),
    "CORP_BOND": dict(level=(82, 104), move=1.4, half_spread=0.20, sens=(1_000, 25_000)),
    "IRS":       dict(level=(3.1, 4.4), move=0.12, half_spread=0.15, sens=(1_000, 40_000)),
    "FX_FWD":    dict(level=(-80, 160), move=6.0, half_spread=1.0, sens=(50, 1_500)),
    "EQ_OPTION": dict(level=(14, 38), move=1.8, half_spread=0.35, sens=(2_000, 45_000)),
    "CDS":       dict(level=(45, 420), move=12.0, half_spread=2.0, sens=(500, 12_000)),
}
# IRS is marked as a par rate in percent but tolerances are in basis points.
UNIT_SCALE = {"IRS": 100.0}
UNDERLYINGS = {
    "GOVT_BOND": ["UST", "BUND", "GILT", "OAT", "JGB", "SWISS CONF"],
    "CORP_BOND": ["NESTLE", "ROCHE", "SIEMENS", "TOTAL", "ORACLE", "VODAFONE", "TATA STEEL", "RELIANCE"],
    "IRS":       ["USD SOFR", "EUR ESTR", "CHF SARON", "GBP SONIA", "INR MIBOR"],
    "FX_FWD":    ["EURUSD", "USDCHF", "USDINR", "GBPUSD", "USDJPY", "AUDUSD"],
    "EQ_OPTION": ["SMI", "SX5E", "SPX", "NIFTY", "NESN", "ROG", "AAPL"],
    "CDS":       ["iTraxx Main", "CDX IG", "iTraxx XO", "Single-name HY", "Single-name IG"],
}
TENORS = ["6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
CCY = {"UST": "USD", "BUND": "EUR", "GILT": "GBP", "OAT": "EUR", "JGB": "JPY", "SWISS CONF": "CHF"}


@dataclass
class Book:
    instruments: pd.DataFrame
    positions: pd.DataFrame
    quotes: pd.DataFrame
    feed_totals: pd.DataFrame
    ground_truth: pd.DataFrame


def month_ends(end: str = "2026-09-30", n: int = 6) -> list[pd.Timestamp]:
    return list(pd.bdate_range(end=end, periods=n * 23, freq="BME")[-n:])


def generate(n_positions: int = 1_200, asof_end: str = "2026-09-30", n_months: int = 6,
             cfg: RunConfig | None = None) -> Book:
    cfg = cfg or RunConfig()
    rng = np.random.default_rng(cfg.seed)
    dates = month_ends(asof_end, n_months)

    # ---- static data ------------------------------------------------------
    rows = []
    desk_names = list(DESKS)
    desk_weights = np.array([0.30, 0.28, 0.14, 0.16, 0.12])
    for i in range(n_positions):
        desk = rng.choice(desk_names, p=desk_weights)
        ac = rng.choice(DESKS[desk])
        und = rng.choice(UNDERLYINGS[ac])
        tenor = rng.choice(TENORS)
        # Liquidity drives both the booked level and how many quotes we can find.
        level = int(rng.choice([1, 2, 3], p={
            "GOVT_BOND": [0.95, 0.05, 0.0], "IRS": [0.0, 0.92, 0.08], "FX_FWD": [0.60, 0.38, 0.02],
            "CORP_BOND": [0.0, 0.80, 0.20], "EQ_OPTION": [0.15, 0.65, 0.20], "CDS": [0.0, 0.75, 0.25],
        }[ac]))
        lo, hi = MARKET[ac]["sens"]
        sens = 4 * float(np.exp(rng.uniform(np.log(lo), np.log(hi)))) * rng.choice([-1, 1], p=[0.35, 0.65])
        if level == 3:
            sens *= 0.6
        lvl_lo, lvl_hi = MARKET[ac]["level"]
        rows.append(dict(
            position_id=f"P{i + 1:05d}", instrument_id=f"{ac[:3]}-{i + 1:05d}", desk=desk, asset_class=ac,
            description=f"{und} {tenor} {ac.replace('_', ' ').title()}",
            currency=CCY.get(und, und[-3:] if ac == "FX_FWD" else "USD"),
            booked_level=level, sensitivity_usd=round(sens, 2),
            unit=ASSET_CLASSES[ac]["unit"], unit_scale=UNIT_SCALE.get(ac, 1.0),
            fv0=rng.uniform(lvl_lo, lvl_hi),
        ))
    inst = pd.DataFrame(rows)

    # ---- fair value paths -------------------------------------------------
    n_t = len(dates)
    fv = np.empty((len(inst), n_t))
    for j, r in inst.iterrows():
        step = MARKET[r.asset_class]["move"] / r.unit_scale
        fv[j] = r.fv0 + np.cumsum(np.r_[0, rng.normal(0, step, n_t - 1)])

    # ---- desk marks, quotes, and injected issues --------------------------
    truth, pos_rows, quote_rows = [], [], []
    frozen = set(rng.choice(len(inst), size=12, replace=False))       # marks that stop moving
    freeze_from = {j: int(rng.integers(1, n_t - 2)) for j in frozen}
    biased_block = (inst.desk == "Credit") & (inst.asset_class == "CORP_BOND")  # late-period desk bias

    for t, d in enumerate(dates):
        for j, r in inst.iterrows():
            mk = MARKET[r.asset_class]
            tol_u = cfg.tolerances[r.asset_class][r.booked_level]
            tol = tol_u / r.unit_scale
            true = fv[j, t]
            mark = true + rng.normal(0, 0.30 * tol)
            issue = None

            if j in frozen and t >= freeze_from[j]:
                issue = "stale_mark"  # mark is overwritten with the frozen value below
            if issue is None:
                u = rng.random()
                favourable = np.sign(r.sensitivity_usd)  # direction that flatters desk P&L
                if u < 0.030:
                    mark = true + favourable * rng.uniform(1.6, 6.0) * tol
                    issue = "off_market_mark"
                elif u < 0.036:
                    mark = true + favourable * rng.uniform(25, 60) * tol
                    issue = "fat_finger"
                elif biased_block[j] and t >= n_t - 2 and u < 0.75:
                    mark = true + favourable * rng.uniform(0.45, 0.9) * tol
                    issue = "desk_bias"
            pos_rows.append(dict(asof=d, position_id=r.position_id, desk_mark=mark, _j=j, _issue=issue))

            # Independent quotes: count and dispersion depend on liquidity.
            n_q = int(rng.choice([0, 1, 2, 3, 4], p={
                1: [0.0, 0.0, 0.03, 0.47, 0.50], 2: [0.005, 0.06, 0.50, 0.39, 0.045], 3: [0.10, 0.45, 0.35, 0.10, 0.0],
            }[r.booked_level]))
            q_issue = None
            if rng.random() < 0.012:
                n_q, q_issue = 0, "no_quotes"
            # Quote noise scales with how hard the position is to price, so an
            # honest mark on an illiquid position still sits inside its (wider) tolerance.
            noise = QUOTE_NOISE_TOL * tol
            hs = mk["half_spread"] * (1 + r.booked_level) / 2 / r.unit_scale
            stale = rng.random() < 0.015 and n_q > 0
            if stale:
                q_issue = "stale_quote"
            srcs = list(rng.choice(SOURCES, size=n_q, replace=False))
            # Only instruments traded on an active market get an exchange close.
            if srcs and r.asset_class in L1_ELIGIBLE and rng.random() < (0.97 if r.booked_level == 1 else 0.04):
                srcs[0] = EXCHANGE_SOURCE
            for s in srcs:
                mid = true + rng.normal(0, noise)
                q_date = d - pd.tseries.offsets.BDay(int(rng.integers(6, 15)) if stale else 0)
                quote_rows.append(dict(asof=d, position_id=r.position_id, source=s, mid=mid,
                                       bid=mid - hs, ask=mid + hs, quote_date=q_date))
            for lbl in (issue, q_issue):
                if lbl:
                    truth.append(dict(asof=d, position_id=r.position_id, issue=lbl))

    pos = pd.DataFrame(pos_rows)
    # Frozen marks: carry the last good mark forward from the freeze point.
    for j in frozen:
        idx = pos.index[pos._j == j]
        f = freeze_from[j]
        pos.loc[idx[f:], "desk_mark"] = pos.loc[idx[f - 1], "desk_mark"]

    # Previous-period consensus is needed for movement checks; marks also carry
    # the date they were last changed, as a front-office system would.
    pos = pos.sort_values(["position_id", "asof"]).reset_index(drop=True)
    pos["desk_mark"] = pos["desk_mark"].astype(float).round(6)
    changed = pos.groupby("position_id")["desk_mark"].diff().fillna(1).ne(0)
    pos["mark_last_changed"] = pos["asof"].where(changed).groupby(pos["position_id"]).ffill()

    # Front-office control totals are struck *before* the feed is loaded; one
    # record is then dropped in transit so the reconciliation has something to find.
    merged = pos.merge(inst[["position_id", "desk", "sensitivity_usd"]], on="position_id")
    feed_totals = (merged.groupby(["asof", "desk"])
                   .agg(fo_count=("position_id", "size"),
                        fo_abs_sensitivity=("sensitivity_usd", lambda s: s.abs().sum()))
                   .reset_index())
    drop_row = pos.index[(pos["asof"] == dates[-3]).to_numpy()][int(rng.integers(0, len(inst)))]
    truth.append(dict(asof=dates[-3], position_id=pos.at[drop_row, "position_id"], issue="dropped_in_feed"))
    pos = pos.drop(index=drop_row)

    positions = pos.drop(columns=["_j", "_issue"]).reset_index(drop=True)
    quotes = pd.DataFrame(quote_rows)
    quotes[["mid", "bid", "ask"]] = quotes[["mid", "bid", "ask"]].round(6)
    ground_truth = pd.DataFrame(truth).drop_duplicates()
    return Book(inst.drop(columns=["fv0"]), positions, quotes, feed_totals, ground_truth)
