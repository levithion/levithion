-- Reporting views. These are the queries an analyst would otherwise rebuild in
-- Excel every month-end; Power BI and the Excel pack read from them.

DROP VIEW IF EXISTS v_latest_run;
CREATE VIEW v_latest_run AS
SELECT run_id FROM ctl_run_log WHERE status = 'OK' ORDER BY started_utc DESC LIMIT 1;

DROP VIEW IF EXISTS v_desk_summary;
CREATE VIEW v_desk_summary AS
SELECT r.asof, r.desk,
       COUNT(*)                                             AS positions,
       SUM(CASE WHEN r.n_quotes > 0 THEN 1 ELSE 0 END)      AS verified,
       SUM(CASE WHEN r.status = 'EXCEPTION' THEN 1 ELSE 0 END) AS exceptions,
       ROUND(SUM(r.pv_adjustment_usd), 0)                   AS net_adjustment_usd,
       ROUND(SUM(ABS(r.pv_adjustment_usd)), 0)              AS gross_adjustment_usd,
       ROUND(SUM(CASE WHEN r.rule_flags LIKE '%BREACH%' OR r.rule_flags LIKE '%FAT_FINGER%'
                           THEN r.pv_adjustment_usd ELSE 0 END), 0)
                                                            AS breach_adjustment_usd,
       ROUND(SUM(r.ava_mpu_usd + r.ava_coc_usd), 0)         AS ava_gross_usd
FROM ipv_result r JOIN v_latest_run l ON r.run_id = l.run_id
GROUP BY r.asof, r.desk;

DROP VIEW IF EXISTS v_level_transfers;
CREATE VIEW v_level_transfers AS
SELECT r.asof, r.position_id, r.desk, r.asset_class, r.booked_level, r.derived_level,
       r.n_quotes, ROUND(r.dispersion_u, 4) AS dispersion_u,
       CASE WHEN r.derived_level > r.booked_level THEN 'Downgrade' ELSE 'Upgrade' END AS direction
FROM ipv_result r JOIN v_latest_run l ON r.run_id = l.run_id
WHERE r.derived_level <> r.booked_level;

DROP VIEW IF EXISTS v_open_exceptions;
CREATE VIEW v_open_exceptions AS
SELECT e.*, i.description, i.asset_class
FROM ipv_exception e JOIN dim_instrument i USING (position_id)
WHERE e.status IN ('OPEN', 'EXPLAINED')
ORDER BY ABS(e.latest_pv_usd) DESC;
