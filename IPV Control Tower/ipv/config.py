"""Control parameters for the IPV run.

Every threshold a reviewer might challenge lives here, in one place, so a
change to policy is a one-line diff that shows up in review and in the run
log (the config hash is recorded with every run).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

# Asset classes and the market observable the desk marks for each.
#   measure   - what the desk marks (price per 100, par rate, vol, spread ...)
#   unit      - how variances are expressed in reports
#   tol       - IPV tolerance per fair-value level, in `unit`
#   stale_bd  - business days after which an independent quote is stale
ASSET_CLASSES: dict[str, dict] = {
    "GOVT_BOND": {"measure": "price", "unit": "pts", "tol": {1: 0.10, 2: 0.25, 3: 0.75}, "stale_bd": 1},
    "CORP_BOND": {"measure": "price", "unit": "pts", "tol": {1: 0.25, 2: 0.50, 3: 1.50}, "stale_bd": 3},
    "IRS":       {"measure": "rate",  "unit": "bp",  "tol": {1: 0.25, 2: 0.50, 3: 2.00}, "stale_bd": 1},
    "FX_FWD":    {"measure": "fwd_pts", "unit": "pips", "tol": {1: 2.0, 2: 5.0, 3: 15.0}, "stale_bd": 1},
    "EQ_OPTION": {"measure": "vol",   "unit": "vol pts", "tol": {1: 0.50, 2: 1.00, 3: 2.50}, "stale_bd": 2},
    "CDS":       {"measure": "spread", "unit": "bp", "tol": {1: 2.0, 2: 5.0, 3: 15.0}, "stale_bd": 2},
}

# IFRS 13 Level 1 needs a quoted price in an active market for an identical asset.
L1_ELIGIBLE = {"GOVT_BOND", "FX_FWD", "EQ_OPTION"}
EXCHANGE_SOURCE = "Exchange close"

DESKS: dict[str, list[str]] = {
    "Rates":        ["GOVT_BOND", "IRS"],
    "Credit":       ["CORP_BOND", "CDS"],
    "FX":           ["FX_FWD"],
    "Equities":     ["EQ_OPTION"],
    "Treasury ALM": ["GOVT_BOND", "IRS", "FX_FWD"],
}


@dataclass(frozen=True)
class RunConfig:
    # A breach needs both: variance outside tolerance AND P&L impact above materiality.
    materiality_usd: float = 25_000.0
    # Severity bands on |PV impact| once a position is in breach.
    severity_bands_usd: tuple[float, float] = (250_000.0, 1_000_000.0)  # (high, critical)
    # Desk mark unchanged for this many consecutive month-ends -> stale-mark flag.
    stale_mark_periods: int = 3
    # A mark more than this many tolerances away is treated as a potential fat-finger.
    fat_finger_multiple: float = 20.0
    # Levelling: dispersion wider than this multiple of the L2 tolerance -> unobservable.
    l3_dispersion_multiple: float = 3.0
    # EBA RTS prudent valuation: aggregation factor for MPU and close-out AVAs.
    ava_diversification: float = 0.5
    # Isolation Forest share of positions expected to be anomalous.
    anomaly_contamination: float = 0.03
    seed: int = 7
    tolerances: dict = field(default_factory=lambda: {k: v["tol"] for k, v in ASSET_CLASSES.items()})

    def fingerprint(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:12]
