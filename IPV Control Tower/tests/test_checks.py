import numpy as np
import pandas as pd
import pytest

from ipv.checks import desk_bias, run_ipv
from ipv.config import RunConfig

D = [pd.Timestamp("2026-07-31"), pd.Timestamp("2026-08-31"), pd.Timestamp("2026-09-30")]


def book(desk_mark, quotes, sens=10_000.0, ac="CORP_BOND", level=2, last_changed=None):
    """One position over three month-ends; quotes given for the last date only (earlier dates get consensus=mark)."""
    inst = pd.DataFrame([dict(position_id="P1", instrument_id="I1", desk="Credit", asset_class=ac, description="x",
                              currency="USD", booked_level=level, sensitivity_usd=sens, unit="pts", unit_scale=1.0)])
    pos = pd.DataFrame(dict(asof=D, position_id="P1", desk_mark=[100.0, 100.5, desk_mark],
                            mark_last_changed=last_changed or D))
    q = []
    for d, mids in zip(D, [[100.0] * 3, [100.5] * 3, quotes]):
        for k, m in enumerate(mids):
            q.append(dict(asof=d, position_id="P1", source=f"S{k}", mid=m, bid=m - 0.1, ask=m + 0.1, quote_date=d))
    return inst, pos, pd.DataFrame(q, columns=["asof", "position_id", "source", "mid", "bid", "ask", "quote_date"])


def last(res):
    return res[res["asof"] == D[-1]].iloc[0]


def test_variance_and_adjustment_sign():
    # Long position (positive sensitivity) marked 2pts above consensus: desk flatters P&L -> negative adjustment.
    r = last(run_ipv(*book(103.0, [101.0, 101.1, 100.9], sens=20_000.0), RunConfig()))
    assert r.consensus == pytest.approx(101.0)
    assert r.variance_u == pytest.approx(2.0)
    assert r.tol_ratio == pytest.approx(4.0)            # CORP_BOND L2 tolerance is 0.5 pts
    assert r.pv_adjustment_usd == pytest.approx(-40_000)
    assert "BREACH" in r.rule_flags and r.status == "EXCEPTION"


def test_breach_needs_materiality():
    # Same variance, tiny position: outside tolerance but below materiality -> no breach.
    r = last(run_ipv(*book(103.0, [101.0, 101.1, 100.9], sens=100.0), RunConfig()))
    assert r.tol_ratio > 1 and "BREACH" not in r.rule_flags and r.status == "PASS"


def test_fat_finger_is_critical():
    r = last(run_ipv(*book(130.0, [101.0, 101.1, 100.9]), RunConfig()))
    assert "FAT_FINGER" in r.rule_flags and r.severity == "CRITICAL"


def test_no_quotes_is_exception_and_level3():
    r = last(run_ipv(*book(101.0, []), RunConfig()))
    assert r.n_quotes == 0 and "NO_QUOTES" in r.rule_flags
    assert r.derived_level == 3 and "LEVEL_TRANSFER" in r.rule_flags
    # Unobservable range -> AVA falls back to the full tolerance.
    assert r.ava_mpu_usd == pytest.approx(10_000 * 0.5)


def test_ava_from_quote_range():
    r = last(run_ipv(*book(101.0, [100.8, 101.0, 101.2]), RunConfig()))
    assert r.dispersion_u == pytest.approx(0.4)
    assert r.ava_mpu_usd == pytest.approx(10_000 * 0.2)
    assert r.ava_coc_usd == pytest.approx(10_000 * 0.1)


def test_stale_mark_after_three_periods():
    r = last(run_ipv(*book(101.0, [101.0] * 3, last_changed=[D[0]] * 3), RunConfig(stale_mark_periods=2)))
    assert r.mark_age_periods == 2 and "STALE_MARK" in r.rule_flags


def test_desk_bias_detects_one_sided_marks():
    rng = np.random.default_rng(0)
    n = 40
    res = pd.DataFrame(dict(asof=D[-1], desk="Credit", asset_class="CORP_BOND",
                            sensitivity_usd=rng.choice([-1, 1], n) * 1_000.0))
    lean = rng.uniform(0.3, 0.8, n)
    res["variance_u"] = np.sign(res.sensitivity_usd) * lean   # every mark leans the flattering way
    res["tol_ratio"] = lean
    b = desk_bias(res)
    assert b.flagged.iloc[0] == 1 and b.share_same_sign.iloc[0] == 1.0

    res["variance_u"] *= rng.choice([-1, 1], n)                # random direction -> no bias
    assert desk_bias(res).flagged.iloc[0] == 0
