"""Self-contained HTML dashboard (a Power BI stand-in anyone can open).

Charts use Chart.js from jsdelivr; data is embedded as JSON so the file
works offline apart from that one script. Colours follow a validated
colour-blind-safe palette with separate light and dark steps.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pandas as pd

from ..facts import usd

SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}


def _md_to_html(md: str) -> str:
    out, in_list = [], False
    for line in md.splitlines():
        line = html.escape(line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"\*(.+?)\*", r"<em>\1</em>", line)
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("### "):
            out.append(f"<h3>{line[4:]}</h3>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def build(path: Path, run_id: str, facts: dict, res: pd.DataFrame, bias: pd.DataFrame,
          controls: pd.DataFrame, detection: pd.DataFrame, det_summary: dict, commentary_md: str) -> Path:
    dates = sorted(res["asof"].unique())
    labels = [pd.Timestamp(d).strftime("%b %y") for d in dates]
    is_breach = res.rule_flags.str.contains("BREACH|FAT_FINGER")
    by_month = res.assign(breach=res.pv_adjustment_usd.where(is_breach, 0),
                          ava=0.5 * (res.ava_mpu_usd + res.ava_coc_usd)).groupby("asof")
    sev = (res[res.status == "EXCEPTION"].groupby(["asof", "severity"]).size().unstack(fill_value=0)
           .reindex(index=dates, columns=["CRITICAL", "HIGH", "MEDIUM"], fill_value=0))
    cur = res[res["asof"] == dates[-1]]
    desk_adj = (cur.assign(b=cur.pv_adjustment_usd.where(cur.rule_flags.str.contains("BREACH|FAT_FINGER"), 0))
                .groupby("desk").b.sum().sort_values())
    lvl = (cur.groupby(["desk", "derived_level"]).size().unstack(fill_value=0)
           .reindex(columns=[1, 2, 3], fill_value=0))
    lvl_pct = lvl.div(lvl.sum(axis=1), axis=0)

    data = {
        "months": labels,
        "breach": [round(-v) for v in by_month.breach.sum().tolist()],  # shown as a reserve (positive)
        "ava": [round(v) for v in by_month.ava.sum().tolist()],
        "sev": {k: sev[k].tolist() for k in sev.columns},
        "desks": desk_adj.index.tolist(),
        "deskAdj": [round(-v) for v in desk_adj.tolist()],
        "lvlDesks": lvl_pct.index.tolist(),
        "lvl": {f"L{c}": [round(x * 100, 1) for x in lvl_pct[c]] for c in lvl_pct.columns},
    }

    exc = cur[cur.status == "EXCEPTION"]
    top = exc.assign(_r=exc.severity.map(SEV_RANK), _a=exc.pv_adjustment_usd.abs().fillna(-1))\
             .sort_values(["_r", "_a"], ascending=[True, False]).head(12)
    rows = "\n".join(
        f"<tr><td>{r.position_id}</td><td>{r.desk}</td><td>{html.escape(r.description)}</td>"
        f"<td><span class='pill {r.severity.lower()}'>{r.severity.title()}</span></td>"
        f"<td>{r.rule_flags.replace('|', ', ')}</td>"
        f"<td class='num'>{'' if pd.isna(r.tol_ratio) else f'{r.tol_ratio:.1f}×'}</td>"
        f"<td class='num'>{'no quote' if pd.isna(r.pv_adjustment_usd) else usd(r.pv_adjustment_usd)}</td></tr>"
        for r in top.itertuples())

    det_rows = "\n".join(
        f"<tr><td>{i.replace('_', ' ')}</td><td class='num'>{int(d.injected)}</td>"
        + "".join(f"<td class='num'><div class='cellbar'><span style='width:{v * 100:.0f}%'></span></div>{v:.0%}</td>"
                  for v in (d.rules, d.ml_triage, d.desk_bias_test, d.any_layer))
        + "</tr>" for i, d in detection.iterrows())

    fb = bias[(bias["asof"] == dates[-1]) & (bias.flagged == 1)]
    bias_html = "".join(
        f"<p><strong>{b.desk} / {b.asset_class.replace('_', ' ').lower()}</strong>: {b.share_same_sign:.0%} of "
        f"in-tolerance marks lean in the desk's favour (mean {b.mean_tol_ratio:.2f}× tolerance, t = {b.t_stat:.1f}). "
        f"Every mark passes its individual check; the book as a whole does not.</p>" for b in fb.itertuples()
    ) or "<p>No desk shows a statistically significant bias this month.</p>"

    ctl = controls[controls["asof"] == dates[-1]]
    fails = ctl[ctl.status != "PASS"]
    ctl_html = (f"<p>{(ctl.status == 'PASS').sum()} of {len(ctl)} data controls passed at {facts['asof']}.</p>" +
                "".join(f"<p class='ctl {s.status.lower()}'><strong>{s.status}</strong> {s.control} · {s.scope} — "
                        f"{html.escape(s.detail)}</p>" for s in fails.itertuples()))
    hist_fail = controls[(controls.status == "FAIL")]
    if len(hist_fail) and not len(fails):
        h = hist_fail.iloc[0]
        ctl_html += (f"<p class='ctl fail'><strong>Earlier FAIL</strong> {h.control} · {h.scope} on "
                     f"{pd.Timestamp(h['asof']).strftime('%d %b %Y')} — {html.escape(h.detail)}</p>")

    kpis = [
        ("Positions verified", facts["positions"], f"{facts['coverage_pct']} independently sourced"),
        ("Open exceptions", facts["exceptions"], f"{facts['critical']} critical · prior {facts['exceptions_prior']}"),
        ("Proposed IPV reserve", facts["breach_adj_usd"].lstrip("-"), f"prior {facts['breach_adj_prior_usd'].lstrip('-')}"),
        ("Aggregated AVA", facts["ava_usd"], "MPU + close-out, 50% diversified"),
        ("Level transfers", f"{facts['level_downgrades']} ↓ / {facts['level_upgrades']} ↑",
         f"{facts['l3_positions']} positions at Level 3"),
        ("ML review queue", facts["ml_review_queue"], "rule-compliant but unusual"),
    ]
    kpi_html = "".join(f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div><div class='s'>{s}</div></div>"
                       for k, v, s in kpis)

    page = TEMPLATE.format(
        asof=facts["asof"], run_id=run_id, kpis=kpi_html, rows=rows, det_rows=det_rows,
        fp=f"{det_summary['rule_false_positive_rate']:.1%}", ml_fp=f"{det_summary['ml_review_rate_on_clean']:.1%}",
        bias=bias_html, controls=ctl_html, commentary=_md_to_html(commentary_md),
        data=json.dumps(data),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page)
    return path


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IPV Control Tower</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {{
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --s1: #2a78d6; --s2: #eb6834;
  --q1: #86b6ef; --q2: #2a78d6; --q3: #104281;
  --critical: #d03b3b; --serious: #ec835a; --warning: #fab219; --good: #0ca30c;
  --crit-bg: #fbe4e4; --ser-bg: #fdebe3; --warn-bg: #fff4d6;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926;
    --q1: #86b6ef; --q2: #3987e5; --q3: #184f95;
    --crit-bg: #3a1d1d; --ser-bg: #3a261c; --warn-bg: #3a3118;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926;
  --q1: #86b6ef; --q2: #3987e5; --q3: #184f95;
  --crit-bg: #3a1d1d; --ser-bg: #3a261c; --warn-bg: #3a3118;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1240px; margin: 0 auto; padding: 24px 16px 48px; }}
header {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: 8px; margin-bottom: 20px; }}
h1 {{ font-size: 22px; margin: 0; }}
h2 {{ font-size: 15px; margin: 0 0 12px; }}
h3 {{ font-size: 15px; margin: 0 0 8px; }}
.sub {{ color: var(--ink-2); font-size: 13px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px; }}
.tile, .card {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 14px 16px; min-width: 0; }}
.tile .k {{ color: var(--ink-2); font-size: 12px; }}
.tile .v {{ font-size: 24px; font-weight: 600; margin: 2px 0; }}
.tile .s {{ color: var(--muted); font-size: 12px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; margin-bottom: 12px; }}
.chart {{ position: relative; height: 240px; }}
.note {{ color: var(--muted); font-size: 12px; margin: 8px 0 0; }}
.scroll {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; color: var(--ink-2); font-weight: 600; border-bottom: 1px solid var(--axis); padding: 6px 8px; white-space: nowrap; }}
td {{ border-bottom: 1px solid var(--grid); padding: 6px 8px; vertical-align: top; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.pill {{ display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; color: var(--ink); }}
.pill::before {{ content: "●"; margin-right: 4px; }}
.pill.critical {{ background: var(--crit-bg); }} .pill.critical::before {{ color: var(--critical); }}
.pill.high {{ background: var(--ser-bg); }} .pill.high::before {{ color: var(--serious); }}
.pill.medium {{ background: var(--warn-bg); }} .pill.medium::before {{ color: var(--warning); }}
.cellbar {{ display: inline-block; width: 60px; height: 6px; background: var(--grid); border-radius: 3px; margin-right: 6px; vertical-align: middle; }}
.cellbar span {{ display: block; height: 100%; background: var(--s1); border-radius: 3px; }}
.ctl.fail strong {{ color: var(--critical); }} .ctl.warn strong {{ color: var(--serious); }}
.commentary p {{ margin: 0 0 10px; }} .commentary hr {{ border: 0; border-top: 1px solid var(--grid); }}
.commentary em {{ color: var(--muted); }}
button.theme {{ background: var(--surface); color: var(--ink-2); border: 1px solid var(--ring); border-radius: 8px; padding: 4px 10px; cursor: pointer; font: inherit; font-size: 12px; }}
</style>
</head>
<body>
<main>
<header>
  <div>
    <h1>IPV Control Tower — month-end {asof}</h1>
    <div class="sub">Independent Price Verification · prudent valuation · fair value levelling · run {run_id} · synthetic data</div>
  </div>
  <button class="theme" id="theme">Toggle theme</button>
</header>

<section class="tiles">{kpis}</section>

<section class="grid">
  <div class="card"><h2>Proposed IPV reserve vs aggregated AVA (USD)</h2><div class="chart"><canvas id="trend"></canvas></div>
    <p class="note">Reserve = revaluation of breached positions to independent consensus.</p></div>
  <div class="card"><h2>Open exceptions by severity</h2><div class="chart"><canvas id="sev"></canvas></div></div>
</section>
<section class="grid">
  <div class="card"><h2>Proposed reserve by desk, {asof} (USD)</h2><div class="chart"><canvas id="desk"></canvas></div></div>
  <div class="card"><h2>Fair value hierarchy by desk (derived, % of positions)</h2><div class="chart"><canvas id="lvl"></canvas></div></div>
</section>

<section class="grid">
  <div class="card commentary"><h2>Draft commentary</h2>{commentary}</div>
  <div class="card"><h2>Hidden in tolerance: desk bias test</h2>{bias}
    <h2 style="margin-top:16px">Data controls</h2>{controls}</div>
</section>

<section class="card" style="margin-bottom:12px"><h2>Largest open exceptions</h2><div class="scroll">
<table><thead><tr><th>Position</th><th>Desk</th><th>Instrument</th><th>Severity</th><th>Rule hits</th><th class="num">× tolerance</th><th class="num">Adjustment</th></tr></thead>
<tbody>{rows}</tbody></table></div></section>

<section class="card"><h2>Detection against injected issues (all months)</h2><div class="scroll">
<table><thead><tr><th>Issue type</th><th class="num">Injected</th><th class="num">Rules</th><th class="num">ML triage</th><th class="num">Desk-bias test</th><th class="num">Any layer</th></tr></thead>
<tbody>{det_rows}</tbody></table></div>
<p class="note">Rule false-positive rate on clean position-months: {fp}. ML review rate on clean positions: {ml_fp}.
Off-market marks below the materiality threshold are deliberately not raised by the rules.</p></section>
</main>

<script>
const D = {data};
const charts = [];
function css(v) {{ return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }}
function money(v) {{ const a = Math.abs(v); return (a >= 1e6 ? (v/1e6).toFixed(1) + 'm' : a >= 1e3 ? (v/1e3).toFixed(0) + 'k' : v.toFixed(0)); }}
function draw() {{
  charts.forEach(c => c.destroy()); charts.length = 0;
  const ink2 = css('--ink-2'), grid = css('--grid'), muted = css('--muted'), surface = css('--surface');
  Chart.defaults.color = ink2;
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  const axes = (stacked, fmt, horizontal) => {{
    const val = {{ stacked, beginAtZero: true, grid: {{ color: grid }}, border: {{ display: false }}, ticks: {{ color: muted, callback: fmt }} }};
    const cat = {{ stacked, grid: {{ display: false }}, border: {{ color: css('--axis') }}, ticks: {{ color: muted }} }};
    return horizontal ? {{ x: val, y: cat }} : {{ x: cat, y: val }};
  }};
  const base = {{ responsive: true, maintainAspectRatio: false, interaction: {{ mode: 'index', intersect: false }},
    plugins: {{ legend: {{ position: 'top', align: 'start', labels: {{ boxWidth: 10, boxHeight: 10, usePointStyle: true }} }} }} }};
  charts.push(new Chart(document.getElementById('trend'), {{ type: 'line',
    data: {{ labels: D.months, datasets: [
      {{ label: 'Proposed IPV reserve', data: D.breach, borderColor: css('--s1'), backgroundColor: css('--s1'), borderWidth: 2, pointRadius: 4, pointBorderColor: surface, pointBorderWidth: 2 }},
      {{ label: 'Aggregated AVA', data: D.ava, borderColor: css('--s2'), backgroundColor: css('--s2'), borderWidth: 2, pointRadius: 4, pointBorderColor: surface, pointBorderWidth: 2 }} ] }},
    options: {{ ...base, scales: axes(false, v => money(v)),
      plugins: {{ ...base.plugins, tooltip: {{ callbacks: {{ label: c => ` ${{c.dataset.label}}: USD ${{money(c.raw)}}` }} }} }} }} }}));
  const sevCol = {{ CRITICAL: css('--critical'), HIGH: css('--serious'), MEDIUM: css('--warning') }};
  charts.push(new Chart(document.getElementById('sev'), {{ type: 'bar',
    data: {{ labels: D.months, datasets: Object.keys(D.sev).map(k => ({{ label: k[0] + k.slice(1).toLowerCase(), data: D.sev[k],
      backgroundColor: sevCol[k], borderColor: surface, borderWidth: {{ top: 2 }}, borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 36 }})) }},
    options: {{ ...base, scales: axes(true, v => v) }} }}));
  charts.push(new Chart(document.getElementById('desk'), {{ type: 'bar',
    data: {{ labels: D.desks, datasets: [{{ label: 'Proposed reserve', data: D.deskAdj, backgroundColor: css('--s1'), borderRadius: 4, borderSkipped: 'start', maxBarThickness: 22 }}] }},
    options: {{ ...base, indexAxis: 'y', interaction: {{ mode: 'nearest', axis: 'y', intersect: false }}, scales: axes(false, v => money(v), true),
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: c => ` USD ${{money(c.raw)}}` }} }} }} }} }}));
  const q = {{ L1: css('--q1'), L2: css('--q2'), L3: css('--q3') }};
  charts.push(new Chart(document.getElementById('lvl'), {{ type: 'bar',
    data: {{ labels: D.lvlDesks, datasets: Object.keys(D.lvl).map(k => ({{ label: 'Level ' + k.slice(1), data: D.lvl[k],
      backgroundColor: q[k], borderColor: surface, borderWidth: 1, maxBarThickness: 22 }})) }},
    options: {{ ...base, indexAxis: 'y', interaction: {{ mode: 'index', axis: 'y', intersect: false }}, scales: {{ ...axes(true, v => v + '%', true), x: {{ ...axes(true, v => v + '%', true).x, max: 100 }} }},
      plugins: {{ ...base.plugins, tooltip: {{ callbacks: {{ label: c => ` ${{c.dataset.label}}: ${{c.raw}}%` }} }} }} }} }}));
}}
document.getElementById('theme').onclick = () => {{
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = dark ? 'light' : 'dark'; draw();
}};
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
draw();
</script>
</body>
</html>
"""
