# Power BI model

`python -m ipv run` writes a star schema to `output/powerbi/`. This guide builds the
report from it in about 20 minutes. A `.pbix` is not committed because it is a binary
that can't be reviewed in a diff; the model is fully described by the CSVs, the
relationships below and [measures.dax](measures.dax).

## 1. Load

*Get data → Text/CSV*, load all six files:

| Table | Grain | Key columns |
|---|---|---|
| `dim_position` | one row per position | `position_id`, `desk`, `asset_class`, `booked_level`, `sensitivity_usd` |
| `dim_date` | one row per month-end | `asof`, `month`, `month_index`, `is_latest` |
| `fact_ipv` | position × month-end | every IPV result, AVA, flags, anomaly score |
| `fact_exception` | one row per exception episode | `exception_id`, `first_seen`, `periods_open`, `status`, `reviewer` |
| `fact_control` | control × scope × month-end | `control`, `status`, `detail` |
| `fact_desk_bias` | desk × asset class × month-end | `mean_tol_ratio`, `t_stat`, `flagged` |

In Power Query set `asof`, `first_seen`, `last_seen` to **Date** and `is_breach`,
`is_latest` to **True/False**. Sort `dim_date[month]` by `month_index`. Mark `dim_date`
as a date table on `asof`.

## 2. Relationships

```
dim_position 1 ──* fact_ipv *── 1 dim_date
dim_position 1 ──* fact_exception
dim_date     1 ──* fact_control      (asof)
dim_date     1 ──* fact_desk_bias    (asof)
```

All single-direction, dimension → fact.

## 3. Measures

Create a blank table `_Measures` and paste each measure from `measures.dax`.

## 4. Pages

**Month-end overview** — slicer: `dim_date[month]` (single select, default latest)
- Cards: `Coverage %`, `Exceptions` (with `Exceptions MoM Δ` as subtitle), `Proposed IPV Reserve`, `Aggregated AVA`, `Level 3 %`, `ML Review Queue`
- Line: `Proposed IPV Reserve` and `Aggregated AVA` by `dim_date[month]` (ignore the month slicer via *Edit interactions*)
- Stacked column: `Exceptions` by month, legend `fact_ipv[severity]`
- Bar: `Proposed IPV Reserve` by `dim_position[desk]`

**Exception workbench** — matrix of `fact_exception` joined to `dim_position[description]`,
conditional formatting on `severity`; drill-through page per `position_id` showing the
mark vs consensus history (`fact_ipv[desk_mark]`, `fact_ipv[consensus]` by month).

**Prudent valuation & levelling** — `AVA MPU` / `AVA Close-out` by desk and asset class;
100% stacked bar of `Positions` by desk with legend `fact_ipv[derived_level]`;
table of level transfers (`Level Downgrades`, `Level Upgrades`).

**Controls & bias** — `fact_control` table filtered to `status <> "PASS"`;
`fact_desk_bias` scatter of `mean_tol_ratio` vs `t_stat`, flagged points highlighted.

## 5. Row-level security (optional)

Role `Desk` with DAX filter on `dim_position`: `[desk] = LOOKUPVALUE(desk_map[desk], desk_map[email], USERPRINCIPALNAME())`
lets each trading desk see only its own exceptions when the report is shared.

## 6. Refresh

Point the CSV sources at a shared folder and schedule `python -m ipv run --out <folder>`
before the gateway refresh. The CSVs and the Excel pack are written by the same run from
the same fact sheet, so the report and the pack cannot disagree.
