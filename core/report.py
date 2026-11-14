"""HTML report. Three writer agents (each with its own format system prompt, all on the Nosana
model) write the prose; every fact, number, link and table is rendered from the result in code.
Model output is untrusted text: it is escaped, never inserted as HTML."""
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, field_validator

from core import llm
from core.schema import DOMAINS, MARKET_NAMES

NON_PERMISSIVE = re.compile(r"GPL|Affero|SSPL|EUPL|Commons Clause", re.I)
NO_MODEL_NOTE = "summary generated without the model"

# ---------- format system prompts (one per writer agent) ----------
EXEC_SUMMARY_SYS = """You write the executive summary of a release-readiness triage report.
FORMAT: a JSON object {"summary": "<text>"}. The text is 3 to 5 plain sentences, one paragraph,
no markdown, no bullet points, no HTML, no headings.
RULES:
- Use the counts exactly as given in the input; do not add, round or re-count.
- Name the markets and the most serious issues using finding titles from the input only.
- Describe this as pre-counsel triage. Never say the product is cleared, approved, safe to launch
  or compliant, and never give a legal conclusion.
- Do not mention anything that is not in the input."""

MARKET_BRIEF_SYS = """You write one short brief per target market for a release-readiness triage report.
FORMAT: a JSON object {"briefs": [{"market": "<market code from the input>", "brief": "<text>"}]}
with exactly one entry per market in the input, in the same order. Each brief is 2 to 4 plain
sentences, no markdown, no bullet points, no HTML.
RULES:
- Say what blocks launch in that market, citing finding titles copied from the input only.
- If a market has no blockers, say "no blockers found in these checks" and mention its warnings.
- Never say the product is cleared or compliant. Do not invent laws, dates or findings."""

REMEDIATION_SYS = """You write an ordered remediation plan for a release-readiness triage report.
FORMAT: a JSON object {"steps": [{"step": "<one imperative sentence>",
"finding_ids": ["<id from the input>", ...], "effort": "S" | "M" | "L",
"owner": "engineering" | "legal" | "product"}]}. At most 8 steps, most urgent first.
RULES:
- Every step must list at least one finding id copied exactly from the input. Group findings that
  one change fixes (the same issue in several markets) into one step.
- effort: S = under a day, M = days, L = weeks. owner: who does the work.
- No markdown, no HTML. Do not propose steps for issues that are not in the input."""


# ---------- writer output models ----------
class ExecSummary(BaseModel):
    summary: str


class MarketBrief(BaseModel):
    market: str
    brief: str


class MarketBriefs(BaseModel):
    briefs: list[MarketBrief] = []


class Step(BaseModel):
    step: str
    finding_ids: list[str] = []
    effort: Literal["S", "M", "L"] = "M"
    owner: Literal["engineering", "legal", "product"] = "engineering"

    @field_validator("effort", mode="before")
    @classmethod
    def _effort(cls, v):
        v = str(v or "M").strip().upper()[:1]
        return v if v in ("S", "M", "L") else "M"

    @field_validator("owner", mode="before")
    @classmethod
    def _owner(cls, v):
        v = str(v or "").strip().lower()
        return v if v in ("engineering", "legal", "product") else "engineering"

    @field_validator("finding_ids", mode="before")
    @classmethod
    def _ids(cls, v):
        if v is None:
            return []
        return [str(i) for i in (v if isinstance(v, list) else [v])]


class RemediationPlan(BaseModel):
    steps: list[Step] = []


# ---------- helpers ----------
def _clean(text) -> str:
    """The report never uses the word 'compliant', including in model-written text."""
    text = "" if text is None else str(text)
    text = re.sub(r"\bnon[- ]?compliant\b", "not meeting the requirement", text, flags=re.I)
    return re.sub(r"\bcompliant\b", "meeting the requirement", text, flags=re.I)


def esc(text) -> str:
    return html.escape(_clean(text), quote=True)


def link(url, label=None) -> str:
    url = str(url or "")
    if urlsplit(url).scheme not in ("http", "https"):          # never emit javascript: etc.
        return esc(label or url)
    text = esc(label) if label and label != url else html.escape(url, quote=True)   # URLs are shown verbatim
    return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{text}</a>'


def all_findings(result: dict) -> list[dict]:
    return [f for c in result.get("cells") or [] for f in c.get("findings") or []]


def counts(result: dict) -> tuple[int, int]:
    fs = all_findings(result)
    return (sum(1 for f in fs if f.get("severity") == "blocker"),
            sum(1 for f in fs if f.get("severity") == "warning"))


def verdict(result: dict) -> str:
    b, w = counts(result)
    if b == 0 and w == 0:
        return "no blockers found in these checks"
    if b == 0:
        return f"no blockers found in these checks / {w} warning{'s' if w != 1 else ''}"
    return f"{b} blocker{'s' if b != 1 else ''} / {w} warning{'s' if w != 1 else ''}"


def _market_name(code: str) -> str:
    return MARKET_NAMES.get(code, code)


def _brief_input(result: dict) -> dict:
    b, w = counts(result)
    return {
        "blockers": b, "warnings": w,
        "markets": [{"market": m, "name": _market_name(m)} for m in result.get("markets") or []],
        "findings": [{"id": f.get("id"), "market": f.get("market"), "domain": f.get("domain"),
                      "severity": f.get("severity"), "title": f.get("title"),
                      "fix_type": f.get("fix_type"), "fix_summary": f.get("fix_summary")}
                     for f in all_findings(result)],
        "cells_without_analysis": [f"{c.get('market')} {c.get('domain')}: {c.get('status')}"
                                   for c in result.get("cells") or [] if c.get("status") != "ok"],
    }


# ---------- deterministic fallbacks ----------
def fallback_summary(result: dict) -> str:
    b, w = counts(result)
    markets = ", ".join(_market_name(m) for m in result.get("markets") or []) or "the selected markets"
    failed = sum(1 for c in result.get("cells") or [] if c.get("status") != "ok")
    text = (f"This pre-counsel triage checked the repository for {markets} across "
            f"{len(DOMAINS)} domains and found {b} blocker{'s' if b != 1 else ''} and "
            f"{w} warning{'s' if w != 1 else ''}.")
    if failed:
        text += f" {failed} market × domain cell{'s' if failed != 1 else ''} could not be analysed and are marked in the matrix."
    return text + " Each finding below links a line of code or a missing document to a dated source."


def fallback_brief(result: dict, market: str) -> str:
    fs = [f for f in all_findings(result) if f.get("market") == market]
    b = [f for f in fs if f.get("severity") == "blocker"]
    w = [f for f in fs if f.get("severity") == "warning"]
    if not b:
        return f"No blockers found in these checks; {len(w)} warning{'s' if len(w) != 1 else ''}."
    titles = "; ".join(str(f.get("title")) for f in b[:4])
    return (f"{len(b)} blocker{'s' if len(b) != 1 else ''} and {len(w)} warning{'s' if len(w) != 1 else ''}. "
            f"Blockers: {titles}{'…' if len(b) > 4 else ''}.")


def fallback_plan(result: dict) -> list[dict]:
    """One step per distinct fix, blockers first."""
    groups: dict[tuple, dict] = {}
    for f in sorted(all_findings(result), key=lambda f: f.get("severity") != "blocker"):
        ev = f.get("evidence") or {}
        key = (ev.get("file"), ev.get("line"), ev.get("missing_artifact") or "", f.get("fix_type"))
        g = groups.setdefault(key, {"step": f.get("fix_summary") or f.get("title") or "",
                                    "finding_ids": [], "effort": "M",
                                    "owner": "engineering" if f.get("fix_type") == "code" else "legal"})
        g["finding_ids"].append(f.get("id"))
    return list(groups.values())[:12]


# ---------- writer agents ----------
def write_exec_summary(result: dict) -> str:
    out = llm.json_call(EXEC_SUMMARY_SYS, json.dumps(_brief_input(result)), ExecSummary).summary.strip()
    if not out:
        raise ValueError("empty summary")
    return out


def write_market_briefs(result: dict) -> dict[str, str]:
    out = llm.json_call(MARKET_BRIEF_SYS, json.dumps(_brief_input(result)), MarketBriefs)
    wanted = set(result.get("markets") or [])
    briefs = {b.market.strip().upper(): b.brief.strip() for b in out.briefs
              if b.market.strip().upper() in wanted and b.brief.strip()}
    if not briefs:
        raise ValueError("no briefs for the requested markets")
    return briefs


def write_remediation(result: dict) -> list[dict]:
    out = llm.json_call(REMEDIATION_SYS, json.dumps(_brief_input(result)), RemediationPlan)
    known = {f.get("id") for f in all_findings(result)}
    steps = []
    for s in out.steps[:8]:
        ids = [i for i in s.finding_ids if i in known]
        if s.step.strip() and ids and len(ids) == len(s.finding_ids):   # drop steps citing unknown ids
            steps.append({"step": s.step.strip(), "finding_ids": ids, "effort": s.effort, "owner": s.owner})
    if not steps:
        raise ValueError("no valid remediation steps")
    return steps


def run_writers(result: dict, use_llm: bool) -> dict:
    """Returns {summary, briefs, plan, fallbacks:set}. Never raises."""
    out = {"summary": fallback_summary(result),
           "briefs": {m: fallback_brief(result, m) for m in result.get("markets") or []},
           "plan": fallback_plan(result), "fallbacks": {"summary", "briefs", "plan"}}
    if not use_llm or not all_findings(result):
        return out
    jobs = {"summary": write_exec_summary, "briefs": write_market_briefs, "plan": write_remediation}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {k: pool.submit(fn, result) for k, fn in jobs.items()}
        for k, fut in futures.items():
            try:
                val = fut.result(timeout=120)
            except Exception:
                continue
            if k == "briefs":
                out["briefs"].update(val)
                if set(val) >= set(result.get("markets") or []):
                    out["fallbacks"].discard(k)
            else:
                out[k] = val
                out["fallbacks"].discard(k)
    return out


# ---------- rendering ----------
CSS = """
*{box-sizing:border-box}
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 color:#1c2330;background:#f4f5f7;margin:0}
.page{max-width:1020px;margin:0 auto;padding:36px 32px 60px;background:#fff}
h1{font-size:28px;margin:0 0 2px;letter-spacing:-.01em}
h2{font-size:19px;margin:38px 0 12px;padding-bottom:6px;border-bottom:2px solid #1c2330}
h3{font-size:15px;margin:22px 0 8px;color:#39465a;text-transform:uppercase;letter-spacing:.05em}
p{margin:0 0 10px}
a{color:#1a56c4;word-break:break-word}
.sub{color:#5b6677;font-size:13px}
.meta{display:grid;grid-template-columns:150px 1fr;gap:4px 14px;margin:18px 0 0;font-size:13.5px}
.meta dt{color:#5b6677}.meta dd{margin:0;word-break:break-word}
.verdict{margin:22px 0 6px;padding:16px 20px;border-radius:8px;font-size:22px;font-weight:700}
.verdict.bad{background:#fdecec;color:#9b1c1c;border:1px solid #f3b9b9}
.verdict.warn{background:#fff6dd;color:#7a5600;border:1px solid #ecd58a}
.verdict.ok{background:#eef2f6;color:#39465a;border:1px solid #d5dbe3}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{border:1px solid #d9dee6;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f1f3f6;font-weight:600}
.matrix td{text-align:center;font-weight:600}
.matrix td.blocker{background:#fbe1e1;color:#8f1d1d}
.matrix td.warning{background:#fff1c9;color:#6f4e00}
.matrix td.none{background:#f4f6f8;color:#5b6677;font-weight:400}
.matrix td.failed{background:#ececec;color:#555;font-weight:400;font-style:italic}
.matrix th.row{text-transform:capitalize}
.small{font-size:12px;color:#6b7686;font-weight:400}
.finding{border:1px solid #d9dee6;border-left:5px solid #c9ced6;border-radius:6px;padding:14px 16px;
 margin:0 0 14px;page-break-inside:avoid;break-inside:avoid}
.finding.blocker{border-left-color:#c62828}.finding.warning{border-left-color:#d79a00}
.finding h4{margin:0 0 6px;font-size:16px}
.chip{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
 padding:2px 8px;border-radius:10px;margin-right:6px;vertical-align:middle}
.chip.blocker{background:#c62828;color:#fff}.chip.warning{background:#e8b100;color:#3a2c00}
.chip.origin{background:#e7ebf1;color:#39465a}.chip.fix{background:#e3f0e6;color:#1e5a2c}
.kv{display:grid;grid-template-columns:110px 1fr;gap:6px 12px;margin-top:8px;font-size:13.5px}
.kv dt{color:#5b6677}.kv dd{margin:0}
pre{background:#f6f8fa;border:1px solid #e1e5ea;border-radius:5px;padding:8px 10px;margin:4px 0 0;
 font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f1f3f6;padding:1px 4px;border-radius:3px}
.prose{background:#f8f9fb;border:1px solid #e1e5ea;border-radius:6px;padding:14px 16px}
.note{font-size:12px;color:#6b7686;margin-top:6px}
ol.plan{padding-left:22px}ol.plan li{margin:0 0 8px}
footer{margin-top:44px;padding-top:14px;border-top:1px solid #d9dee6;color:#5b6677;font-size:13px}
@media print{body{background:#fff}.page{padding:0;max-width:none}a{color:#1c2330;text-decoration:none}
 h2{page-break-after:avoid}.verdict,.matrix td,.chip{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _note(fallbacks: set, key: str) -> str:
    if key in fallbacks:
        return f'<div class="note">{NO_MODEL_NOTE}</div>'
    return '<div class="note">written by the model from the findings below; the figures and links in this report are not model-written</div>'


def _matrix(result: dict) -> str:
    markets = result.get("markets") or []
    cells = {(c.get("market"), c.get("domain")): c for c in result.get("cells") or []}
    head = "".join(f"<th>{esc(_market_name(m))}</th>" for m in markets)
    rows = []
    for d in DOMAINS:
        tds = []
        for m in markets:
            c = cells.get((m, d))
            if c is None:
                tds.append('<td class="failed">not run</td>')
                continue
            if c.get("status") != "ok":
                tds.append(f'<td class="failed">{esc(c.get("status"))}</td>')
                continue
            fs = c.get("findings") or []
            b = sum(1 for f in fs if f.get("severity") == "blocker")
            w = sum(1 for f in fs if f.get("severity") == "warning")
            parts = ([f"{b} blocker{'s' if b != 1 else ''}"] if b else []) + \
                    ([f"{w} warning{'s' if w != 1 else ''}"] if w else [])
            cls = "blocker" if b else "warning" if w else "none"
            extra = ""
            if c.get("cached") and c.get("fetched_at"):
                extra = f'<div class="small">rules cached at {esc(c.get("fetched_at"))}</div>'
            tds.append(f'<td class="{cls}">{esc(", ".join(parts) or "none found")}{extra}</td>')
        rows.append(f'<tr><th class="row">{esc(d)}</th>{"".join(tds)}</tr>')
    return f'<table class="matrix"><tr><th>Domain</th>{head}</tr>{"".join(rows)}</table>'


def _finding(f: dict) -> str:
    sev = f.get("severity") if f.get("severity") in ("blocker", "warning") else "warning"
    ev = f.get("evidence") or {}
    rule = f.get("rule") or {}
    if ev.get("file") and ev.get("snippet"):
        loc = esc(f"{ev.get('file')}:{ev.get('line')}" if ev.get("line") else ev.get("file"))
        evidence = f"<code>{loc}</code><pre>{html.escape(str(ev.get('snippet')), quote=True)}</pre>"
    elif ev.get("missing_artifact"):
        where = f" (<code>{esc(ev.get('file'))}</code>)" if ev.get("file") else ""
        evidence = f"Missing: {esc(ev.get('missing_artifact'))}{where}"
    else:
        evidence = "—"
    source = link(rule.get("source_url"), rule.get("source_title") or rule.get("source_url"))
    fetched = f' <span class="small">fetched {esc(rule.get("fetched_at"))}</span>' if rule.get("fetched_at") else ""
    return f"""<div class="finding {sev}" id="f-{esc(f.get('id'))}">
<h4><span class="chip {sev}">{sev}</span>{esc(f.get('title'))}</h4>
<div><span class="chip origin">origin: {esc(f.get('origin', 'model'))}</span><span class="chip fix">fix: {esc(f.get('fix_type'))}</span><span class="small">id {esc(f.get('id'))}</span></div>
<dl class="kv">
<dt>Why</dt><dd>{esc(f.get('why'))}</dd>
<dt>Evidence</dt><dd>{evidence}</dd>
<dt>Rule</dt><dd><strong>{esc(rule.get('name'))}</strong> — {esc(rule.get('requirement'))}</dd>
<dt>Source</dt><dd>{source}{fetched}</dd>
<dt>Fix</dt><dd>{esc(f.get('fix_summary'))}</dd>
</dl></div>"""


def _findings(result: dict, briefs: dict, fallbacks: set) -> str:
    out = []
    for m in result.get("markets") or []:
        out.append(f"<h2>{esc(_market_name(m))}</h2>")
        out.append(f'<div class="prose"><p>{esc(briefs.get(m, ""))}</p>{_note(fallbacks, "briefs")}</div>')
        for d in DOMAINS:
            cell = next((c for c in result.get("cells") or []
                         if c.get("market") == m and c.get("domain") == d), None)
            out.append(f"<h3>{esc(d)}</h3>")
            if cell is None:
                out.append('<p class="sub">Not run.</p>')
            elif cell.get("status") != "ok":
                out.append(f'<p class="sub">No analysis for this cell: {esc(cell.get("status"))}.</p>')
            elif not cell.get("findings"):
                out.append('<p class="sub">None found in these checks.</p>')
            else:
                order = sorted(cell["findings"], key=lambda f: f.get("severity") != "blocker")
                out.extend(_finding(f) for f in order)
                if cell.get("dropped_count"):
                    out.append(f'<p class="small">{int(cell["dropped_count"])} candidate finding(s) in this cell were dropped by the evidence check.</p>')
    return "\n".join(out)


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="sub">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _plan(plan: list[dict], result: dict, fallbacks: set) -> str:
    titles = {f.get("id"): f.get("title") for f in all_findings(result)}
    items = []
    for s in plan:
        refs = ", ".join(f'<a href="#f-{esc(i)}" title="{esc(titles.get(i))}">{esc(i)}</a>'
                         for i in s.get("finding_ids") or [])
        items.append(f'<li>{esc(s.get("step"))} <span class="chip origin">{esc(s.get("owner"))}</span>'
                     f'<span class="chip origin">effort {esc(s.get("effort"))}</span>'
                     f'<div class="small">findings: {refs}</div></li>')
    if not items:
        return '<p class="sub">No remediation steps: no findings.</p>'
    return f'<ol class="plan">{"".join(items)}</ol>{_note(fallbacks, "plan")}'


def _appendix(result: dict) -> str:
    scan = result.get("scan") or {}
    facts = result.get("facts") or {}
    parts = ["<h2>Appendix A — scanner output (deterministic)</h2>",
             "<h3>Secrets (detect-secrets)</h3>",
             _table(["File", "Line", "Type"],
                    [[f"<code>{esc(s.get('file'))}</code>", esc(s.get("line")), esc(s.get("type"))]
                     for s in scan.get("secrets") or []], "None detected."),
             "<h3>Known vulnerabilities (pip-audit)</h3>",
             _table(["Package", "Version", "Advisory", "Fixed in"],
                    [[esc(v.get("package")), esc(v.get("version")), esc(v.get("vuln_id")),
                      esc(", ".join(map(str, v.get("fix_versions") or [])) or "—")]
                     for v in scan.get("vulns") or []], "None detected."),
             "<h3>Non-permissive licences (pip-licenses)</h3>",
             _table(["Package", "Version", "Licence"],
                    [[esc(l.get("package")), esc(l.get("version")), esc(l.get("licence"))]
                     for l in scan.get("licences") or [] if NON_PERMISSIVE.search(str(l.get("licence")))],
                    f"None among {len(scan.get('licences') or [])} packages."),
             "<h3>Static analysis</h3>",
             _table(["File", "Line", "Rule", "Severity", "Message"],
                    [[f"<code>{esc(s.get('file'))}</code>", esc(s.get("line")),
                      esc(s.get("rule") or s.get("test_id") or s.get("check_id")),
                      esc(s.get("severity")), esc(s.get("issue") or s.get("message") or s.get("issue_text"))]
                     for s in scan.get("sast") or []], "No static-analysis results in this run."),
             "<h2>Appendix B — sourced enrichment</h2>",
             _table(["Kind", "Subject", "What the source says", "Source", "Fetched"],
                    [[esc(e.get("kind")), esc(e.get("subject")), esc(e.get("summary")),
                      link(e.get("source_url")), esc(e.get("fetched_at"))]
                     for e in facts.get("enrichment") or []], "No enrichment items.")]
    return "\n".join(parts)


def _context(result: dict) -> str:
    ctx = result.get("context") or {}
    if not ctx:
        return '<p class="sub">No intake answers were provided for this run.</p>'
    rows = []
    for k, v in ctx.items():
        if isinstance(v, (list, tuple)):
            v = ", ".join(map(str, v))
        elif isinstance(v, dict):
            v = json.dumps(v)
        rows.append([esc(str(k).replace("_", " ")), esc(v)])
    return _table(["Question", "Answer"], rows, "")


def build_report(result: dict, use_llm: bool = True) -> str:
    """One self-contained HTML document. Never raises on writer failure."""
    w = run_writers(result, use_llm)
    b, wn = counts(result)
    scan = result.get("scan") or {}
    searches = result.get("searches") or {}
    vclass = "bad" if b else "warn" if wn else "ok"
    dropped = int(result.get("dropped_total") or 0)
    sample = ('<p class="verdict warn" style="font-size:14px">SAMPLE DATA — hand-written example, '
              'not a live run.</p>') if result.get("sample") else ""
    error = f'<p class="verdict bad" style="font-size:14px">Run error: {esc(result.get("error"))}</p>' \
        if result.get("error") else ""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LaunchGate report — {esc(result.get('repo_url'))}</title>
<style>{CSS}</style></head>
<body><div class="page">
<h1>LaunchGate release triage</h1>
<div class="sub">Pre-counsel triage. Not legal advice.</div>
{sample}{error}
<dl class="meta">
<dt>Repository</dt><dd>{link(result.get('repo_url'))}</dd>
<dt>Report generated</dt><dd>{esc(generated)}</dd>
<dt>Markets</dt><dd>{esc(', '.join(_market_name(m) for m in result.get('markets') or []))}</dd>
<dt>Domains</dt><dd>{esc(', '.join(DOMAINS))}</dd>
<dt>Model</dt><dd>{esc(result.get('llm_model'))} on <code>{esc(result.get('llm_host'))}</code></dd>
<dt>Sandbox</dt><dd><code>{esc(scan.get('sandbox_id'))}</code> (Daytona)</dd>
<dt>Web searches</dt><dd>{esc(searches.get('live', 0))} live, {esc(searches.get('cached', 0))} from cache (Tavily)</dd>
</dl>
<div class="verdict {vclass}">{esc(verdict(result))}</div>

<h2>Executive summary</h2>
<div class="prose"><p>{esc(w['summary'])}</p>{_note(w['fallbacks'], 'summary')}</div>

<h2>Market × domain matrix</h2>
{_matrix(result)}

<h2>Remediation plan</h2>
{_plan(w['plan'], result, w['fallbacks'])}

<h2>Intake context</h2>
{_context(result)}

{_findings(result, w['briefs'], w['fallbacks'])}

{_appendix(result)}

<h2>Method</h2>
<p>The repository was cloned and scanned inside an isolated sandbox; no repository code ran on the
host. A model read the scan output and key files to build a fact sheet, current requirements were
retrieved by web search for each market and domain, and the model proposed findings.</p>
<p><strong>Evidence check.</strong> Code, not the model, decides which findings are kept. A finding is
dropped unless (1) its rule's source is a page the search returned for that cell, (2) its quoted
snippet occurs within three lines of the cited line in the file read from the sandbox, or it names
a missing artifact, and (3) it states a fix. In this run <strong>{dropped}</strong> candidate
finding{'s were' if dropped != 1 else ' was'} dropped by that check.</p>
<p>The executive summary, market briefs and remediation plan are written by the model and may be
imprecise; every count, table, snippet and link in this report is rendered directly from the run data.</p>

<footer>Pre-counsel triage. Not legal advice. Sources are linked with their fetch time so a person can check them.</footer>
</div></body></html>"""
