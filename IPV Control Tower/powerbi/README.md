# Power BI model

`python -m ipv run` writes a star schema to `output/powerbi/`. This folder holds a
**Power BI Project** (PBIP) that sits on top of it: a semantic model (tables,
relationships, measures) and a four-page report, stored as text (TMDL and PBIR) so
changes show up in a diff, unlike a binary `.pbix`.

## Quick start (Power BI web, works on a Mac)

1. `make run` writes `output/powerbi/IPV_Star_Schema.xlsx`: six sheets, each a named Excel
   table (`dim_position`, `dim_date`, `fact_ipv`, `fact_exception`, `fact_control`,
   `fact_desk_bias`).
2. At [app.powerbi.com](https://app.powerbi.com): *New → Report → Pick a published
   dataset / Upload a file*, or *My workspace → Upload → Browse this computer* and pick the
   workbook. When asked, choose *Import* and select all six tables.
3. Open the semantic model and choose *Open data model*. In the model view drag
   `position_id` and `asof` between tables to create the relationships in section 2 below.
   Set `dim_date` as the date table and sort `month` by `month_index`.
4. *New measure*: paste each measure from [measures.dax](measures.dax) (one at a time; the
   web editor takes one measure per entry). Do these first: `Positions`, `Exceptions`,
   `Coverage %`, `Proposed IPV Reserve`, `Aggregated AVA`.
5. *Create a report* and build the pages in section 4 below.

Refresh: re-upload the workbook after each `make run` (the service replaces the dataset
of the same name), or move the file to OneDrive/SharePoint and use *Connect* so the
service picks up the new file automatically.

## Power BI Desktop (Windows) project

Open [IPV.pbip](IPV.pbip) in Power BI Desktop, set the `DataFolder` parameter to the
absolute path of `output/powerbi`, and apply. This is optional.

| Path | What it is |
|---|---|
| [IPV.SemanticModel/](IPV.SemanticModel/) | TMDL model: six CSV-backed tables, five relationships, 22 measures in `_Measures` |
| [IPV.Report/](IPV.Report/) | PBIR report: Month-end overview, Exception workbench, Prudent valuation & levelling, Controls & bias |
| [measures.dax](measures.dax) | The same measures as plain DAX, for review and reuse |

The project was written by hand from the schema, not saved out of Desktop. If Desktop
reports a problem loading a visual, delete that visual's folder under
`IPV.Report/definition/pages/*/visuals/` and rebuild it in the canvas; the model is
independent of the report.

Steps 1-4 below are the manual build, kept as the reference for what the project contains.

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
