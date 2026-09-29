"""SQLite persistence: load inputs, store results, maintain the exception log."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

RESULT_COLS = [
    "run_id", "asof", "position_id", "desk", "asset_class", "booked_level", "derived_level",
    "desk_mark", "consensus", "n_quotes", "dispersion_u", "bid_ask_u", "variance_u", "tolerance_u",
    "tol_ratio", "pv_adjustment_usd", "ava_mpu_usd", "ava_coc_usd", "quote_age_bd", "mark_age_periods",
    "mark_move_gap", "rule_flags", "anomaly_score", "anomaly_flag", "status", "severity",
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((SQL_DIR / "schema.sql").read_text())
    con.executescript((SQL_DIR / "views.sql").read_text())
    return con


def _dates_to_text(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df


def load_inputs(con, inst, pos, quotes, feed) -> None:
    """Replace the input tables with this delivery (inputs are snapshots, not history)."""
    for t in ("src_quote", "src_desk_mark", "src_fo_control_totals", "dim_instrument"):
        con.execute(f"DELETE FROM {t}")
    cols = ["position_id", "instrument_id", "desk", "asset_class", "description", "currency",
            "booked_level", "sensitivity_usd", "unit", "unit_scale"]
    inst[cols].to_sql("dim_instrument", con, if_exists="append", index=False)
    _dates_to_text(pos).to_sql("src_desk_mark", con, if_exists="append", index=False)
    _dates_to_text(quotes).to_sql("src_quote", con, if_exists="append", index=False)
    _dates_to_text(feed).to_sql("src_fo_control_totals", con, if_exists="append", index=False)


def save_results(con, run_id: str, res: pd.DataFrame, controls: pd.DataFrame, bias: pd.DataFrame) -> None:
    r = _dates_to_text(res.assign(run_id=run_id))[RESULT_COLS]
    r["quote_age_bd"] = r.quote_age_bd.astype("float")
    r.to_sql("ipv_result", con, if_exists="append", index=False)
    _dates_to_text(controls.assign(run_id=run_id)).to_sql("ctl_check", con, if_exists="append", index=False)
    if len(bias):
        _dates_to_text(bias.assign(run_id=run_id)).to_sql("ipv_desk_bias", con, if_exists="append", index=False)


def upsert_exceptions(con, res: pd.DataFrame, preparer: str = "ipv-engine") -> pd.DataFrame:
    """Roll position-level breaches into an exception log that survives reruns.

    A breach that persists across consecutive month-ends is one exception
    with an age, not N new ones. Human fields (status, reviewer, commentary)
    are never overwritten by the engine. Exceptions that no longer breach at
    the latest date are closed automatically ("cleared at source").
    """
    dates = sorted(res["asof"].unique())
    idx = {d: i for i, d in enumerate(dates)}
    exc = res[res.status == "EXCEPTION"].sort_values(["position_id", "asof"]).copy()
    exc["i"] = exc["asof"].map(idx)
    # New episode whenever a month-end is skipped.
    exc["episode"] = (exc.groupby("position_id").i.diff() != 1).cumsum()
    ep = (exc.groupby("episode")
             .agg(position_id=("position_id", "first"), desk=("desk", "first"),
                  first_seen=("asof", "min"), last_seen=("asof", "max"), periods_open=("asof", "size"),
                  latest_flags=("rule_flags", "last"), latest_pv_usd=("pv_adjustment_usd", "last"),
                  severity=("severity", "last"))
             .reset_index(drop=True))
    ep["exception_id"] = "EXC-" + ep.position_id + "-" + ep.first_seen.dt.strftime("%Y%m")
    ep = _dates_to_text(ep)
    latest = pd.Timestamp(dates[-1]).strftime("%Y-%m-%d")

    con.executemany(
        """INSERT INTO ipv_exception (exception_id, position_id, desk, first_seen, last_seen, periods_open,
                                      latest_flags, latest_pv_usd, severity, status, preparer)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
           ON CONFLICT(exception_id) DO UPDATE SET
               last_seen = excluded.last_seen, periods_open = excluded.periods_open,
               latest_flags = excluded.latest_flags, latest_pv_usd = excluded.latest_pv_usd,
               severity = excluded.severity""",
        [(r.exception_id, r.position_id, r.desk, r.first_seen, r.last_seen, int(r.periods_open),
          r.latest_flags, None if pd.isna(r.latest_pv_usd) else float(r.latest_pv_usd), r.severity, preparer)
         for r in ep.itertuples()])
    con.execute("UPDATE ipv_exception SET status = 'CLOSED' WHERE last_seen < ? AND status <> 'CLOSED'", (latest,))
    con.commit()
    return pd.read_sql("SELECT * FROM ipv_exception", con)


def sign_off(con, exception_id: str, reviewer: str, status: str, commentary: str) -> None:
    """Four-eyes sign-off. The CHECK constraint rejects reviewer == preparer."""
    if status not in {"EXPLAINED", "ADJUSTED", "CLOSED"}:
        raise ValueError(f"invalid status {status!r}")
    cur = con.execute("UPDATE ipv_exception SET reviewer = ?, status = ?, commentary = ? WHERE exception_id = ?",
                      (reviewer, status, commentary, exception_id))
    if cur.rowcount == 0:
        raise ValueError(f"unknown exception {exception_id!r}")
    con.commit()
