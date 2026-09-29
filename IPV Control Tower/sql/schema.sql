-- IPV Control Tower schema.
-- Plain ANSI SQL on SQLite so it ports to MS Access / SQL Server with
-- type renames only. Inputs (dim_/src_) are loaded as delivered; everything
-- the engine derives lives in ipv_/ctl_ tables keyed by run_id so every
-- number in a report can be traced back to the run that produced it.

CREATE TABLE IF NOT EXISTS dim_instrument (
    position_id      TEXT PRIMARY KEY,
    instrument_id    TEXT NOT NULL,
    desk             TEXT NOT NULL,
    asset_class      TEXT NOT NULL,
    description      TEXT,
    currency         TEXT,
    booked_level     INTEGER NOT NULL CHECK (booked_level IN (1, 2, 3)),
    sensitivity_usd  REAL NOT NULL,   -- PV change per 1 unit of the marked observable
    unit             TEXT NOT NULL,
    unit_scale       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS src_desk_mark (
    asof               DATE NOT NULL,
    position_id        TEXT NOT NULL REFERENCES dim_instrument(position_id),
    desk_mark          REAL,
    mark_last_changed  DATE,
    PRIMARY KEY (asof, position_id)
);

CREATE TABLE IF NOT EXISTS src_quote (
    asof         DATE NOT NULL,
    position_id  TEXT NOT NULL REFERENCES dim_instrument(position_id),
    source       TEXT NOT NULL,
    mid          REAL NOT NULL,
    bid          REAL,
    ask          REAL,
    quote_date   DATE NOT NULL,
    PRIMARY KEY (asof, position_id, source)
);

CREATE TABLE IF NOT EXISTS src_fo_control_totals (
    asof                DATE NOT NULL,
    desk                TEXT NOT NULL,
    fo_count            INTEGER NOT NULL,
    fo_abs_sensitivity  REAL NOT NULL,
    PRIMARY KEY (asof, desk)
);

CREATE TABLE IF NOT EXISTS ctl_run_log (
    run_id        TEXT PRIMARY KEY,
    started_utc   TEXT NOT NULL,
    config_hash   TEXT NOT NULL,
    input_hash    TEXT NOT NULL,
    n_positions   INTEGER,
    n_quotes      INTEGER,
    runtime_sec   REAL,
    status        TEXT
);

CREATE TABLE IF NOT EXISTS ctl_check (
    run_id     TEXT NOT NULL,
    asof       DATE NOT NULL,
    control    TEXT NOT NULL,     -- e.g. COMPLETENESS, RECON_COUNT
    scope      TEXT,
    status     TEXT NOT NULL,     -- PASS / FAIL / WARN
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS ipv_result (
    run_id             TEXT NOT NULL,
    asof               DATE NOT NULL,
    position_id        TEXT NOT NULL,
    desk               TEXT, asset_class TEXT, booked_level INTEGER, derived_level INTEGER,
    desk_mark          REAL, consensus REAL, n_quotes INTEGER,
    dispersion_u       REAL,      -- max-min of quotes, in tolerance units
    bid_ask_u          REAL,
    variance_u         REAL,      -- desk mark minus consensus, in tolerance units
    tolerance_u        REAL,
    tol_ratio          REAL,      -- |variance| / tolerance
    pv_adjustment_usd  REAL,      -- signed adjustment to bring desk PV to independent PV
    ava_mpu_usd        REAL,
    ava_coc_usd        REAL,
    quote_age_bd       INTEGER,
    mark_age_periods   INTEGER,
    mark_move_gap      REAL,      -- desk mark move minus market move, in tolerances
    rule_flags TEXT,      -- pipe-separated rule hits
    anomaly_score      REAL,
    anomaly_flag       INTEGER,
    status             TEXT,      -- PASS / EXCEPTION
    severity           TEXT,
    PRIMARY KEY (run_id, asof, position_id)
);

CREATE TABLE IF NOT EXISTS ipv_desk_bias (
    run_id TEXT, asof DATE, desk TEXT, asset_class TEXT,
    n INTEGER, mean_tol_ratio REAL, t_stat REAL, share_same_sign REAL, flagged INTEGER
);

CREATE TABLE IF NOT EXISTS ipv_exception (
    exception_id   TEXT PRIMARY KEY,   -- stable across runs: position + first breach date
    position_id    TEXT NOT NULL,
    desk           TEXT NOT NULL,
    first_seen     DATE NOT NULL,
    last_seen      DATE NOT NULL,
    periods_open   INTEGER NOT NULL,
    latest_flags   TEXT,
    latest_pv_usd  REAL,
    severity       TEXT,
    status         TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN / EXPLAINED / ADJUSTED / CLOSED
    preparer       TEXT,
    reviewer       TEXT,
    commentary     TEXT,
    CHECK (reviewer IS NULL OR reviewer <> preparer)   -- four-eyes, enforced by the database
);
