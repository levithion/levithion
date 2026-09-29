"""CLI: `python -m ipv run [--ai] [--positions N] [--out DIR]`, `python -m ipv signoff ...`."""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from . import db, pipeline


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ipv", description="IPV Control Tower")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the month-end IPV process end to end")
    r.add_argument("--out", type=Path, default=Path("output"))
    r.add_argument("--positions", type=int, default=1_200)
    r.add_argument("--ai", action="store_true", help="draft commentary with Claude (needs Anthropic credentials)")

    s = sub.add_parser("signoff", help="four-eyes sign-off of an exception")
    s.add_argument("exception_id")
    s.add_argument("--reviewer", required=True)
    s.add_argument("--status", required=True, choices=["EXPLAINED", "ADJUSTED", "CLOSED"])
    s.add_argument("--comment", required=True)
    s.add_argument("--db", type=Path, default=Path("output/ipv.db"))

    si = sub.add_parser("signoffs-import", help="load reviewer decisions exported by the Excel toolkit")
    si.add_argument("csv", type=Path)
    si.add_argument("--db", type=Path, default=Path("output/ipv.db"))

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if a.cmd == "signoffs-import":
        import csv
        con = db.connect(a.db)
        ok = bad = 0
        with open(a.csv, newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    db.sign_off(con, row["exception_id"], row["reviewer"], row["status"], row["commentary"])
                    ok += 1
                except (sqlite3.IntegrityError, ValueError) as e:
                    bad += 1
                    print(f"rejected {row['exception_id']}: {e}", file=sys.stderr)
        print(f"{ok} sign-off(s) applied, {bad} rejected")
        return 1 if bad else 0

    if a.cmd == "signoff":
        con = db.connect(a.db)
        try:
            db.sign_off(con, a.exception_id, a.reviewer, a.status, a.comment)
        except sqlite3.IntegrityError:
            print("Rejected: reviewer must differ from preparer (four-eyes).", file=sys.stderr)
            return 1
        print(f"{a.exception_id} -> {a.status} by {a.reviewer}")
        return 0

    res = pipeline.run(out_dir=a.out, n_positions=a.positions, use_ai=a.ai)
    f, d = res.facts, res.detection_summary
    print(f"""
IPV month-end {f['asof']}  (run {res.run_id}, {res.runtime_sec:.1f}s)
  positions verified     {f['positions']}  coverage {f['coverage_pct']}
  open exceptions        {f['exceptions']}  ({f['critical']} critical, {f['high']} high)
  proposed IPV reserve   {f['breach_adj_usd']}
  aggregated AVA         {f['ava_usd']}
  level transfers        {f['level_downgrades']} down / {f['level_upgrades']} up
  ML review queue        {f['ml_review_queue']}
  rule false-positive    {d['rule_false_positive_rate']:.1%} of clean position-months
  commentary             {res.commentary.source}{' - ' + res.commentary.note if res.commentary.note else ''}

outputs in {res.out_dir}/: dashboard.html, IPV_Pack_*.xlsx, ipv.db, powerbi/*.csv, commentary.md
""")
    print(res.detection.to_string(float_format=lambda x: f"{x:.0%}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
