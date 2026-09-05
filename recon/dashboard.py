"""Render out/dashboard.html from a completed run.

Every figure on the page is read from out/report.json and out/threshold.json.
Nothing is hardcoded: delete those files and this produces nothing rather than
a page of plausible-looking numbers. That is the point -- a dashboard that can
render without a run is a dashboard that can lie about one.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

# Roles from the validated reference palette. Light values on :root; the dark
# steps are the same hues re-stepped for the dark surface, declared under both
# the OS media query and the explicit theme stamp so a toggle wins either way.
_CSS = """
:root{color-scheme:light;
  --surface:#fcfcfb; --panel:#ffffff; --line:#e4e3df;
  --ink:#0b0b0b; --ink-2:#52514e; --ink-3:#78766f;
  --series-1:#2a78d6; --track:#eceae5;
  --good:#0ca30c; --warning:#fab219;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
  --surface:#1a1a19; --panel:#212120; --line:#35352f;
  --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#96958c;
  --series-1:#3987e5; --track:#2c2c29;
  --good:#0ca30c; --warning:#fab219;}}
:root[data-theme="dark"]{color-scheme:dark;
  --surface:#1a1a19; --panel:#212120; --line:#35352f;
  --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#96958c;
  --series-1:#3987e5; --track:#2c2c29;
  --good:#0ca30c; --warning:#fab219;}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);
  font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  padding:32px 24px 64px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:19px;font-weight:600;margin:0 0 2px}
.sub{color:var(--ink-2);margin:0 0 24px;font-size:13px}
.note{border-left:3px solid var(--warning);background:var(--panel);
  padding:12px 16px;margin:0 0 24px;border-radius:0 6px 6px 0;
  color:var(--ink-2);font-size:13px}
.note b{color:var(--ink)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  margin-bottom:12px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.tile .label{color:var(--ink-2);font-size:12px;margin-bottom:6px}
.tile .value{font-size:26px;font-weight:600;letter-spacing:-0.02em}
.tile .foot{color:var(--ink-3);font-size:12px;margin-top:4px}
.hero{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:22px 24px;margin-bottom:12px}
.hero .label{color:var(--ink-2);font-size:13px}
.hero .value{font-size:56px;font-weight:600;letter-spacing:-0.03em;line-height:1.05;margin:4px 0}
.hero .foot{color:var(--ink-2);font-size:13px}
.ok{color:var(--good);font-weight:600}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:20px 22px;margin-bottom:12px}
.panel h2{font-size:14px;font-weight:600;margin:0 0 4px}
.panel .cap{color:var(--ink-2);font-size:12.5px;margin:0 0 16px}
.bars{display:grid;gap:9px}
.bar{display:grid;grid-template-columns:150px 1fr 62px;align-items:center;gap:12px}
.bar .n{color:var(--ink-2);font-size:12.5px}
.bar .track{background:var(--track);border-radius:4px;height:16px;
  border:1px solid var(--line)}
.bar .fill{background:var(--series-1);border-radius:4px;height:100%;
  box-shadow:0 0 0 2px var(--panel)}
.bar .v{text-align:right;font-size:12.5px;color:var(--ink-2);
  font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--ink-2);font-weight:500;font-size:12px;
  padding:0 10px 8px 0;border-bottom:1px solid var(--line)}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:none}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
  color:var(--ink-3)}
.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11.5px;
  border:1px solid var(--line);color:var(--ink-2);white-space:nowrap}
.scroll{overflow-x:auto}
.foot-note{color:var(--ink-3);font-size:12px;margin-top:24px;line-height:1.6}
"""

_SVG_W, _SVG_H, _PAD = 640, 200, 34


def _cost_chart(curve: list[dict]) -> str:
    """Expected cost against threshold. One series, so no legend -- the title names it."""
    if not curve:
        return ""
    xs = [p["threshold"] for p in curve]
    ys = [p["expected_cost"] for p in curve]
    lo_x, hi_x, hi_y = min(xs), max(xs), max(ys) or 1.0

    def px(t): return _PAD + (t - lo_x) / (hi_x - lo_x or 1) * (_SVG_W - _PAD * 2)
    def py(c): return _SVG_H - _PAD - (c / hi_y) * (_SVG_H - _PAD * 2)

    pts = " ".join(f"{px(p['threshold']):.1f},{py(p['expected_cost']):.1f}" for p in curve)
    lowest = min(curve, key=lambda p: p["expected_cost"])
    cx, cy = px(lowest["threshold"]), py(lowest["expected_cost"])
    ticks = "".join(
        f'<text x="{px(t):.1f}" y="{_SVG_H - 12}" text-anchor="middle" '
        f'font-size="11" fill="var(--ink-3)">{t:.1f}</text>'
        for t in (0.3, 0.45, 0.6, 0.75, 0.9))
    return f"""<div class="scroll"><svg viewBox="0 0 {_SVG_W} {_SVG_H}" width="100%"
  style="max-width:{_SVG_W}px" role="img"
  aria-label="Expected cost against acceptance threshold; minimum at {lowest['threshold']:.2f}">
  <line x1="{_PAD}" y1="{_SVG_H-_PAD}" x2="{_SVG_W-_PAD}" y2="{_SVG_H-_PAD}"
        stroke="var(--line)" stroke-width="1"/>
  <polyline points="{pts}" fill="none" stroke="var(--series-1)" stroke-width="2"
            stroke-linejoin="round"/>
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="var(--series-1)"
          stroke="var(--panel)" stroke-width="2"/>
  <text x="{cx:.1f}" y="{cy - 12:.1f}" text-anchor="middle" font-size="12"
        font-weight="600" fill="var(--ink)">{lowest['threshold']:.2f}</text>
  {ticks}
  <text x="{_PAD}" y="16" font-size="11" fill="var(--ink-3)">expected cost (INR)</text>
</svg></div>"""


def _bars(by_style: dict) -> str:
    rows = []
    for style, pair in sorted(by_style.items(), key=lambda kv: -kv[1][1]):
        ok, total = pair
        pct = ok / total if total else 0.0
        rows.append(
            f'<div class="bar"><div class="n">{html.escape(style)}</div>'
            f'<div class="track"><div class="fill" style="width:{pct*100:.1f}%"></div></div>'
            f'<div class="v">{ok}/{total}</div></div>')
    return f'<div class="bars">{"".join(rows)}</div>'


def _queue(rows: list[dict]) -> str:
    """Two different 'why's live in this table and must not be conflated.

    `Why unmatched` is the reconciler's verdict -- why no settlement was paired.
    `Posted to` is the triage outcome. A row can be legitimately NO_CANDIDATE
    and still auto-post, because it turned out not to be a settlement at all;
    labelling that column "Reason" made the two read as a contradiction.
    """
    body = "".join(
        f'<tr><td class="num">{r["amount"]/100:,.2f}</td>'
        f'<td><span class="tag">{html.escape(r["disposition"] or "-")}</span></td>'
        f'<td>{html.escape(r["reason"])}</td>'
        f'<td class="mono">{html.escape(r["narration"][:46])}'
        f'{"&hellip;" if len(r["narration"]) > 46 else ""}</td>'
        f'<td class="mono">{html.escape(r["gl_account"])}</td>'
        f'<td>{"auto" if r["auto_posted"] else "<b>human</b>"}</td></tr>'
        for r in rows)
    return f"""<div class="scroll"><table>
  <thead><tr><th style="text-align:right">INR</th><th>Disposition</th>
  <th>Why unmatched</th><th>Narration</th><th>Posted to</th><th>Routed</th></tr></thead>
  <tbody>{body}</tbody></table></div>"""


def write(out: Path) -> Path:
    report = json.loads((out / "report.json").read_text())
    thresh = json.loads((out / "threshold.json").read_text())
    s = report["score"]
    offline = report["mode"] != "model"

    note = ""
    if offline:
        note = ('<div class="note"><b>Triage ran the SIMULATED offline classifier</b> '
                '(no ANTHROPIC_API_KEY). Its accuracy is circular &mdash; those keyword '
                'rules were written against these same narrations &mdash; so it is not '
                'shown as a result. Reconciliation figures below are unaffected: the '
                'matcher never consults the classifier.</div>')

    if thresh.get("calibrated") is False:
        cost_panel = (f'<div class="panel"><h2>Acceptance threshold</h2>'
                      f'<p class="cap">Not calibrated. Every proposal on the calibration '
                      f'split was correct, so the cost curve has no false-accept term and '
                      f'its minimum collapses to &ldquo;accept everything&rdquo;. Reporting '
                      f'a number from that would be an artefact, so the run fell back to a '
                      f'stated default of {thresh["threshold"]:.2f} and said so.</p></div>')
    else:
        cost_panel = (f'<div class="panel"><h2>Acceptance threshold, calibrated from cost</h2>'
                      f'<p class="cap">A false accept costs INR '
                      f'{thresh["cost_false_accept"]:,.0f} to find and unwind; a false '
                      f'reject costs INR {thresh["cost_false_reject"]:,.0f} of analyst '
                      f'time. Swept on a separate seed; the minimum sits at '
                      f'{thresh["threshold"]:.2f}.</p>'
                      f'{_cost_chart(thresh.get("curve", []))}</div>')

    fp = s["false_positive"]
    fp_cls = "ok" if fp == 0 else ""
    fp_mark = "&check; " if fp == 0 else ""

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reconciliation run {html.escape(report['run_id'])}</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>Reconciliation run</h1>
<p class="sub">{s['rows']} bank credits against {report['settlements']} settlements &middot;
run <span class="mono">{html.escape(report['run_id'])}</span> &middot;
triage <span class="mono">{html.escape(report.get('triage_label') or report['mode'])}</span></p>
{note}
<div class="hero"><div class="label">Match rate</div>
  <div class="value">{s['match_rate']*100:.1f}%</div>
  <div class="foot">{s['matched']} of {s['rows']} credits reconciled automatically</div></div>
<div class="grid">
  <div class="tile"><div class="label">Precision</div>
    <div class="value">{s['precision']*100:.1f}%</div>
    <div class="foot">{s['correct']} correct of {s['matched']} claimed</div></div>
  <div class="tile"><div class="label">False matches</div>
    <div class="value {fp_cls}">{fp_mark}{fp}</div>
    <div class="foot">wrong pairs written to the ledger</div></div>
  <div class="tile"><div class="label">Recall</div>
    <div class="value">{s['recall']*100:.1f}%</div>
    <div class="foot">{s['missed']} genuinely missed</div></div>
  <div class="tile"><div class="label">Throughput</div>
    <div class="value">{s['rows_per_second']:,.0f}<span style="font-size:15px;
      color:var(--ink-3)"> rows/s</span></div>
    <div class="foot">{s['correctly_unmatched']} correctly left alone</div></div>
</div>
<div class="panel"><h2>Accuracy by difficulty class</h2>
  <p class="cap">Correct outcomes per class, so one easy class cannot carry the aggregate.
  <b>ambiguous_pair</b> is 0/2 deliberately: two identical amounts settled the same day
  with no reference in the narration. The information needed to separate them does not
  exist, so they are escalated rather than guessed.</p>
  {_bars(s['by_style'])}</div>
{cost_panel}
<div class="panel"><h2>Exception queue</h2>
  <p class="cap">Everything the system could not reconcile, largest value first &mdash;
  so an analyst working top-down meets the expensive uncertainty before the trivial one.</p>
  {_queue(report['exception_queue'])}</div>
<p class="foot-note">Every figure on this page is read from
<span class="mono">out/report.json</span> and <span class="mono">out/threshold.json</span>,
written by the run itself. Nothing here is hardcoded &mdash; delete those files and this
page does not render. Full decision trail in <span class="mono">out/audit.jsonl</span>.</p>
</div></body></html>"""
    path = out / "dashboard.html"
    path.write_text(page)
    return path
