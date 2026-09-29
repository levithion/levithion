"""IPV engine: data controls, price verification, prudent valuation, levelling.

Conventions
-----------
* `*_u` columns are in the asset class's tolerance unit (price points, bp,
  pips, vol points) so one tolerance table works across asset classes.
* `pv_adjustment_usd` is the change to desk PV if the position were
  revalued at the independent consensus. Negative means the desk mark is
  flattering P&L and a reserve would be booked.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ASSET_CLASSES, EXCHANGE_SOURCE, L1_ELIGIBLE, RunConfig

EXCEPTION_FLAGS = {"BREACH", "FAT_FINGER", "NO_QUOTES", "STALE_MARK", "STALE_QUOTE"}


# --------------------------------------------------------------------------- controls
def data_controls(inst: pd.DataFrame, pos: pd.DataFrame, quotes: pd.DataFrame,
                  feed: pd.DataFrame) -> pd.DataFrame:
    """Completeness, uniqueness, sanity and front-office reconciliation.

    These run before any valuation check: a variance computed on an
    incomplete feed is worse than no variance at all.
    """
    out = []

    def add(asof, control, scope, ok, detail, warn=False):
        out.append(dict(asof=asof, control=control, scope=scope,
                        status="PASS" if ok else ("WARN" if warn else "FAIL"), detail=detail))

    loaded = pos.merge(inst[["position_id", "desk", "sensitivity_usd"]], on="position_id", how="left")
    for asof, g in loaded.groupby("asof"):
        add(asof, "COMPLETENESS", "all", g.desk_mark.notna().all(),
            f"{g.desk_mark.isna().sum()} positions without a desk mark")
        add(asof, "REFERENTIAL", "all", g.desk.notna().all(),
            f"{g.desk.isna().sum()} positions missing static data")
        dup = g.duplicated("position_id").sum()
        add(asof, "UNIQUENESS", "all", dup == 0, f"{dup} duplicate position keys")

        q = quotes[quotes["asof"] == asof]
        bad = ((q.bid > q.mid) | (q.mid > q.ask)).sum()
        add(asof, "QUOTE_SANITY", "all", bad == 0, f"{bad} quotes with bid > mid or mid > ask")
        cov = g.position_id.isin(q.position_id).mean()
        add(asof, "QUOTE_COVERAGE", "all", cov >= 0.97, f"{cov:.1%} of positions have ≥1 quote", warn=True)

        fo = feed[feed["asof"] == asof].set_index("desk")
        ours = g.groupby("desk").agg(n=("position_id", "size"),
                                     s=("sensitivity_usd", lambda s: s.abs().sum()))
        for desk, f in fo.iterrows():
            n = int(ours.n.get(desk, 0))
            s = float(ours.s.get(desk, 0.0))
            add(asof, "RECON_COUNT", desk, n == f.fo_count, f"loaded {n} vs front office {int(f.fo_count)}")
            add(asof, "RECON_SENSITIVITY", desk, abs(s - f.fo_abs_sensitivity) < 1.0,
                f"loaded {s:,.0f} vs front office {f.fo_abs_sensitivity:,.0f} USD/unit")
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- IPV
def _business_days(start: pd.Series, end: pd.Series) -> np.ndarray:
    s = start.values.astype("datetime64[D]")
    e = end.values.astype("datetime64[D]")
    return np.busday_count(s, e)


def aggregate_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    g = quotes.groupby(["asof", "position_id"])
    agg = g.agg(n_quotes=("mid", "size"), consensus=("mid", "median"),
                q_max=("mid", "max"), q_min=("mid", "min"),
                bid_ask=("ask", "median"), bid_med=("bid", "median"),
                latest_quote=("quote_date", "max"),
                has_exchange=("source", lambda s: (s == EXCHANGE_SOURCE).any())).reset_index()
    agg["dispersion"] = agg.q_max - agg.q_min
    agg["bid_ask"] = agg.bid_ask - agg.bid_med
    return agg.drop(columns=["q_max", "q_min", "bid_med"])


def run_ipv(inst: pd.DataFrame, pos: pd.DataFrame, quotes: pd.DataFrame,
            cfg: RunConfig) -> pd.DataFrame:
    df = (pos.merge(inst, on="position_id", how="inner")
             .merge(aggregate_quotes(quotes), on=["asof", "position_id"], how="left"))
    df["n_quotes"] = df.n_quotes.fillna(0).astype(int)
    df["has_exchange"] = df.has_exchange.astype("boolean").fillna(False).astype(bool)
    df = df.sort_values(["position_id", "asof"]).reset_index(drop=True)

    tol = cfg.tolerances
    df["tolerance_u"] = [tol[a][lvl] for a, lvl in zip(df.asset_class, df.booked_level)]
    tol_l1 = df.asset_class.map(lambda a: tol[a][1])
    tol_l2 = df.asset_class.map(lambda a: tol[a][2])
    sc = df.unit_scale

    # Price verification
    df["variance_u"] = (df.desk_mark - df.consensus) * sc
    df["tol_ratio"] = df.variance_u.abs() / df.tolerance_u
    df["pv_adjustment_usd"] = df.sensitivity_usd * (df.consensus - df.desk_mark) * sc
    df["dispersion_u"] = df.dispersion * sc
    df["bid_ask_u"] = df.bid_ask * sc

    # Prudent valuation (EBA RTS on prudent valuation, core approach, simplified):
    # market price uncertainty from the observed quote range, close-out cost from
    # half the bid/ask. With fewer than two quotes the range is unobservable, so
    # the full tolerance is used as a conservative proxy.
    abs_sens = df.sensitivity_usd.abs()
    mpu_width = np.where(df.n_quotes >= 2, df.dispersion_u / 2, df.tolerance_u)
    df["ava_mpu_usd"] = abs_sens * mpu_width
    df["ava_coc_usd"] = abs_sens * df.bid_ask_u.fillna(df.tolerance_u) / 2

    # Fair value levelling (IFRS 13): observability of the input, not the booking.
    wide = df.dispersion_u > cfg.l3_dispersion_multiple * tol_l2
    derived = np.where(df.n_quotes < 2, 3, np.where(wide, 3, 2))
    l1 = (df.asset_class.isin(L1_ELIGIBLE) & df.has_exchange
          & (df.n_quotes >= 2) & (df.dispersion_u <= 1.5 * tol_l1))
    derived = np.where(l1, 1, derived)
    # A level 3 booking is only upgraded on strong evidence (3+ tight quotes).
    keep_l3 = (df.booked_level == 3) & ~((df.n_quotes >= 3) & (df.dispersion_u <= tol_l2))
    df["derived_level"] = np.where(keep_l3, 3, derived).astype(int)

    # Staleness
    stale_bd = df.asset_class.map(lambda a: ASSET_CLASSES[a]["stale_bd"])
    age = pd.Series(pd.NA, index=df.index, dtype="Int64")
    has_q = df.latest_quote.notna()
    age[has_q] = _business_days(df.latest_quote[has_q], df["asof"][has_q])
    df["quote_age_bd"] = age
    periods = {d: i for i, d in enumerate(sorted(df["asof"].unique()))}
    df["mark_age_periods"] = df["asof"].map(periods) - df.mark_last_changed.map(periods)

    # Movement: did the desk move its mark with the market?
    g = df.groupby("position_id")
    d_mark = g.desk_mark.diff() * sc
    d_mkt = g.consensus.diff() * sc
    df["mark_move_gap"] = (d_mark - d_mkt) / df.tolerance_u

    # Rule flags
    flags = pd.DataFrame(index=df.index)
    flags["NO_QUOTES"] = df.n_quotes == 0
    flags["BREACH"] = (df.tol_ratio > 1) & (df.pv_adjustment_usd.abs() >= cfg.materiality_usd)
    flags["FAT_FINGER"] = df.tol_ratio > cfg.fat_finger_multiple
    flags["STALE_QUOTE"] = (df.quote_age_bd.fillna(0) > stale_bd) & (df.booked_level < 3)
    flags["STALE_MARK"] = df.mark_age_periods >= cfg.stale_mark_periods
    flags["SINGLE_SOURCE"] = (df.n_quotes == 1) & (df.booked_level < 3)
    flags["LEVEL_TRANSFER"] = df.derived_level != df.booked_level
    df["rule_flags"] = flags.apply(lambda r: "|".join(c for c in flags.columns if r[c]), axis=1)
    is_exc = flags[list(EXCEPTION_FLAGS)].any(axis=1)
    df["status"] = np.where(is_exc, "EXCEPTION", "PASS")

    hi, crit = cfg.severity_bands_usd
    pv = df.pv_adjustment_usd.abs().fillna(0)
    df["severity"] = np.select(
        [~is_exc, flags.FAT_FINGER | (pv >= crit), pv >= hi],
        ["", "CRITICAL", "HIGH"], default="MEDIUM")
    return df


# --------------------------------------------------------------------------- desk bias
def desk_bias(res: pd.DataFrame, min_n: int = 15) -> pd.DataFrame:
    """Detect systematic optimism hidden *inside* tolerance.

    Every mark can pass its individual check while a whole book leans the
    same way. Orient each in-tolerance variance so that positive means
    "flatters desk P&L", then t-test the mean against zero per desk and
    asset class.
    """
    ok = res[(res.tol_ratio <= 1) & res.variance_u.notna()].copy()
    # pv_adjustment < 0  <=>  sign(sensitivity * variance) > 0
    ok["flattering"] = np.sign(ok.sensitivity_usd * ok.variance_u) * ok.tol_ratio
    rows = []
    for (asof, desk, ac), g in ok.groupby(["asof", "desk", "asset_class"]):
        x = g.flattering.to_numpy()
        if len(x) < min_n:
            continue
        sd = x.std(ddof=1)
        t = x.mean() / (sd / np.sqrt(len(x))) if sd > 0 else 0.0
        rows.append(dict(asof=asof, desk=desk, asset_class=ac, n=len(x),
                         mean_tol_ratio=x.mean(), t_stat=t, share_same_sign=(x > 0).mean(),
                         flagged=int(t > 4 and x.mean() > 0.25)))
    return pd.DataFrame(rows)
