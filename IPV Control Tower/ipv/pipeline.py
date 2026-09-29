"""End-to-end month-end run: load -> control -> verify -> triage -> report."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import ai_commentary, anomaly, checks, db, evaluate, facts as factsheet
from .config import RunConfig
from .generate import Book, generate
from .reporting import dashboard, excel, powerbi

log = logging.getLogger("ipv")


@dataclass
class RunResult:
    run_id: str
    out_dir: Path
    facts: dict
    results: pd.DataFrame
    controls: pd.DataFrame
    bias: pd.DataFrame
    exceptions: pd.DataFrame
    detection: pd.DataFrame
    detection_summary: dict
    commentary: ai_commentary.Commentary
    runtime_sec: float


def _input_hash(book: Book) -> str:
    h = hashlib.sha256()
    for df in (book.instruments, book.positions, book.quotes, book.feed_totals):
        h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()[:16]


def run(out_dir: Path = Path("output"), n_positions: int = 1_200, cfg: RunConfig | None = None,
        use_ai: bool = False, book: Book | None = None) -> RunResult:
    cfg = cfg or RunConfig()
    t0 = time.perf_counter()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    book = book or generate(n_positions=n_positions, cfg=cfg)
    log.info("run %s: %d positions x %d month-ends, %d quotes", run_id, book.instruments.shape[0],
             book.positions["asof"].nunique(), len(book.quotes))

    con = db.connect(out_dir / "ipv.db")
    con.execute("INSERT INTO ctl_run_log (run_id, started_utc, config_hash, input_hash, status) VALUES (?,?,?,?,?)",
                (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), cfg.fingerprint(),
                 _input_hash(book), "RUNNING"))
    db.load_inputs(con, book.instruments, book.positions, book.quotes, book.feed_totals)

    controls = checks.data_controls(book.instruments, book.positions, book.quotes, book.feed_totals)
    log.info("data controls: %s", controls.status.value_counts().to_dict())
    res = anomaly.score(checks.run_ipv(book.instruments, book.positions, book.quotes, cfg), cfg)
    bias = checks.desk_bias(res)
    db.save_results(con, run_id, res, controls, bias)
    exceptions = db.upsert_exceptions(con, res)

    detection, det_summary = evaluate.detection_matrix(res, bias, controls, book.ground_truth)
    f = factsheet.build(res, bias, controls, cfg)
    commentary = ai_commentary.generate(f, out_dir, use_ai=use_ai)
    if commentary.note:
        log.info("commentary: %s (%s)", commentary.source, commentary.note)

    cfg_rows = [(k, str(v)) for k, v in asdict(cfg).items()] + [
        ("run_id", run_id), ("config_hash", cfg.fingerprint()), ("input_hash", _input_hash(book))]
    excel.build(out_dir / f"IPV_Pack_{res['asof'].max():%Y%m}.xlsx", run_id, f, res, exceptions, bias,
                controls, detection, det_summary, cfg_rows, commentary.markdown)
    powerbi.export(out_dir / "powerbi", book.instruments, res, exceptions, controls, bias)
    dashboard.build(out_dir / "dashboard.html", run_id, f, res, bias, controls, detection, det_summary,
                    commentary.markdown)
    (out_dir / "commentary.md").write_text(commentary.markdown)

    runtime = time.perf_counter() - t0
    con.execute("UPDATE ctl_run_log SET n_positions=?, n_quotes=?, runtime_sec=?, status='OK' WHERE run_id=?",
                (len(book.positions), len(book.quotes), round(runtime, 2), run_id))
    con.commit()
    con.close()
    return RunResult(run_id, out_dir, f, res, controls, bias, exceptions, detection, det_summary,
                     commentary, runtime)
