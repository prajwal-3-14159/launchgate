"""LaunchGate UI. Run: streamlit run ui.py"""
import html
import json
import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)   # pick up keys added to .env after the server started

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "samples" / "sample_result.json"
LAST_RUN = ROOT / "last_run.json"
DEMO_REPO = "https://github.com/prajwal-3-14159/launchgate-demo-repo"
MARKETS = {"JP": "Japan", "EU": "European Union", "US": "United States", "IN": "India"}
TESTED = ("JP", "EU")
DOMAINS = ["security", "privacy", "licences", "trade"]
AF_DEFAULT = "http://localhost:8080"
PUBLIC = os.environ.get("LAUNCHGATE_PUBLIC") == "1"   # hosted demo: no patch step, no host settings
PAGES = ["New check", "Results", "Report", "How it works", "About & limits"]

st.set_page_config(page_title="LaunchGate", layout="wide")
ss = st.session_state
ss.setdefault("result", None)
ss.setdefault("log", [])
ss.setdefault("fixes", {})       # finding id -> fix dict
ss.setdefault("drafts", {})      # finding id -> drafted policy text
ss.setdefault("sandbox_deleted", False)
ss.setdefault("report_html", None)
ss.setdefault("tour_open", True)  # start-up tour on first load
ss.setdefault("tour_step", 0)
if "_goto" in ss:                 # page switch requested by the previous run
    ss["nav"] = ss.pop("_goto")

CSS = """
<style>
html, body, [class*="css"], .stMarkdown, button, input, textarea {
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;}
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {display:none !important;}
.block-container {padding-top: 2rem; max-width: 1180px;}
h1, h2, h3, h4 {letter-spacing:-.01em;}
.lg-head {background:linear-gradient(100deg,#12303b 0%,#1c4a52 100%); border-radius:12px; padding:20px 24px; margin-bottom:24px;}
.lg-head .name {font-size:1.45rem; font-weight:700; color:#fff; letter-spacing:-.01em;}
.lg-head .line {color:#cfe3e4; font-size:.98rem; margin-top:4px;}
.lg-sum {color:#334155; font-size:.98rem; margin:-4px 0 16px;}
.lg-cards {display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:8px; margin:8px 0 16px;}
.lg-card {border:1px solid #e2e8f0; border-top:3px solid #cbd5e1; border-radius:10px; padding:12px 16px; background:#fff;
  box-shadow:0 1px 2px rgba(15,23,42,.04);}
.lg-card.red {border-top-color:#d92d20;} .lg-card.amber {border-top-color:#f79009;} .lg-card.green {border-top-color:#12b76a;}
.lg-card .k {font-size:.72rem; color:#64748b; text-transform:uppercase; letter-spacing:.05em; font-weight:600;}
.lg-card .v {font-size:1.75rem; font-weight:700; line-height:1.3; color:#0f172a;}
.lg-card .v.small {font-size:.8rem; font-weight:500; word-break:break-all;}
.lg-card.red .v {color:#b42318;} .lg-card.amber .v {color:#b54708;} .lg-card.green .v {color:#067647;}
.lg-matrix {display:grid; gap:8px; margin-bottom:12px;}
.lg-matrix > div {padding:12px 16px; border-radius:10px;}
.lg-matrix .h {font-weight:650; font-size:.85rem; color:#334155; text-align:center; padding:4px 16px;}
.lg-matrix .r {font-weight:650; font-size:.95rem; color:#0f172a; display:flex; align-items:center; padding-left:4px;}
.lg-cell {font-weight:650; font-size:.98rem; min-height:64px; display:flex; flex-direction:column; justify-content:center;
  align-items:center; text-align:center; border:1px solid transparent;}
.lg-cell small {font-weight:400; font-size:.72rem; color:#64748b; margin-top:2px;}
.lg-cell.blocker {background:#fde8e6; color:#b42318; border-color:#fbd0cc;} .lg-cell.warning {background:#fff1cf; color:#b54708; border-color:#fde3a3;}
.lg-cell.none {background:#e3f6ea; color:#067647; border-color:#c5ebd3;} .lg-cell.status {background:#eef1f4; color:#64748b; font-weight:500; border-color:#e2e8f0;}
.dot {display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:8px; vertical-align:middle;}
.dot.blocker {background:#d92d20;} .dot.warning {background:#f79009;}
.chip {display:inline-block; border-radius:4px; padding:2px 8px; font-size:.75rem; font-weight:600; margin-right:8px;
  border:1px solid transparent;}
.chip.blocker {background:#fef3f2; color:#b42318; border-color:#fecdca;}
.chip.warning {background:#fffaeb; color:#b54708; border-color:#fedf89;}
.chip.scanner, .chip.model, .chip.plain {background:#f8fafc; color:#475569; border-color:#e2e8f0;}
.lg-legend {font-size:.8rem; color:#64748b; margin:0 0 8px;}
.lg-mk {font-size:1.05rem; font-weight:650; color:#0f172a; margin:24px 0 0; padding-bottom:8px; border-bottom:1px solid #e2e8f0;}
.lg-dm {font-size:.78rem; font-weight:600; color:#64748b; text-transform:uppercase; letter-spacing:.05em; margin:16px 0 8px;}
.lg-ok {border:1px solid #abefc6; background:#f6fef9; color:#067647; border-radius:8px; padding:8px 16px; font-weight:600; font-size:.9rem;}
.lg-no {border:1px solid #fedf89; background:#fffaeb; color:#b54708; border-radius:8px; padding:8px 16px; font-weight:600; font-size:.9rem;}
a.pill {display:inline-block; border:1px solid #cbd5e1; border-radius:4px; padding:2px 8px;
  font-size:.78rem; text-decoration:none; margin:4px 8px 4px 0; color:#0f766e;}
a.pill:hover {border-color:#0f766e;}
.lg-foot {font-size:.8rem; color:#64748b; border-top:1px solid #e2e8f0; padding-top:16px; margin-top:32px;}
.lg-step {border-left:2px solid #cbd5e1; padding:0 0 0 16px; margin:8px 0; color:#334155;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)
esc = html.escape


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def when(ts) -> str:
    return str(ts or "")[:16].replace("T", " ") + (" UTC" if ts else "")


# ---------- runners ----------
def _af_execute(base: str, target: str, payload: dict) -> dict:
    """Async execute + poll: the control plane's sync call times out at 90 s by default,
    and a full run takes longer than that."""
    base = base.rstrip("/")
    r = requests.post(f"{base}/api/v1/execute/async/{target}", json={"input": payload}, timeout=30)
    r.raise_for_status()
    execution_id = r.json()["execution_id"]
    deadline = time.time() + 1200
    while time.time() < deadline:
        time.sleep(3)
        body = requests.get(f"{base}/api/v1/executions/{execution_id}", timeout=30).json()
        status = str(body.get("status", "")).lower()
        if status == "succeeded":
            result = body.get("result")
            if not isinstance(result, dict):
                raise ValueError(f"unexpected AgentField result: {str(body)[:200]}")
            return result
        if status in ("failed", "cancelled", "timeout"):
            raise RuntimeError(f"execution {execution_id} {status}: {str(body.get('error'))[:200]}")
    raise TimeoutError(f"execution {execution_id} still running after 20 minutes")


def run_check(repo_url: str, markets: list[str], use_af: bool, af_url: str, log, context: dict) -> dict:
    if use_af:
        try:
            log(f"AgentField: POST {af_url}/api/v1/execute/async/launchgate.launch_check")
            res = _af_execute(af_url, "launchgate.launch_check",
                              {"repo_url": repo_url, "markets": markets, "context": context})
            if "cells" not in res:
                raise ValueError(f"result has no cells: {str(res)[:200]}")
            for line in res.get("log", []):
                log(line)
            res["via"] = "AgentField control plane"
            res.setdefault("context", context)
            return res
        except Exception as exc:
            log(f"AgentField call failed ({type(exc).__name__}: {str(exc)[:160]}); running core.pipeline directly")
    from core.pipeline import run_launch_check
    try:
        res = run_launch_check(repo_url, markets, log=log, context=context)
    except TypeError:                                        # pipeline without the context argument
        res = run_launch_check(repo_url, markets, log=log)
    res["via"] = "core.pipeline (direct)"
    res.setdefault("context", context)
    return res


def run_patch(result: dict, finding: dict, use_af: bool, af_url: str) -> dict:
    payload = {"repo_url": result["repo_url"], "sandbox_id": result["scan"]["sandbox_id"], "finding": finding}
    if use_af:
        try:
            return _af_execute(af_url, "launchgate.fix", payload)
        except Exception as exc:
            ss.log.append(f"AgentField fix call failed ({type(exc).__name__}: {str(exc)[:160]}); "
                          "running core.pipeline directly")
    from core.pipeline import run_fix
    return run_fix(**payload)


def load_result(data: dict, via: str | None = None) -> None:
    ss.fixes, ss.drafts, ss.sandbox_deleted, ss.report_html = {}, {}, False, None
    ss.result = data
    ss.log = list(data.get("log", []))
    if via:
        data.setdefault("via", via)
    ss["_goto"] = "Results"


# ---------- start-up tour ----------
TOUR = [
    ("What LaunchGate does",
     "Give it a public repo and the markets you want to launch in. It returns a **market × domain matrix** of "
     "release blockers. Every finding points at a line of code (or a named missing document) and at a dated "
     "web source you can open."),
    ("Describe your app",
     "On **New check** you describe the product: what it is, how it is distributed, who uses it, which personal "
     "data it handles and where it will launch. The code shows *what* the app does; your answers tell the "
     "agents *in what setting*."),
    ("Read the matrix",
     "Rows are domains (security, privacy, licences, trade), columns are markets. Each cell says "
     "“2 blockers”, “1 warning” or “none found”. Click through to the findings below it for "
     "the code line, the rule, its source and when it was fetched."),
    ("The evidence check",
     "The model proposes findings; **code decides which survive**. A finding is dropped unless its source was "
     "really fetched, its snippet really sits at the cited line (±3), and it states a fix. The number dropped "
     "is shown on every run."),
    ("Patch and report",
     "For a code-fixable blocker, **Generate patch** has CodeAF write a diff and Daytona re-scan it. For a "
     "document gap, **Draft policy section** writes a draft for counsel. The **Report** tab exports everything "
     "as one HTML file. This is pre-counsel triage, not legal advice."),
]


@st.dialog("How LaunchGate works", width="large")
def tour() -> None:
    i = ss.tour_step
    title, body = TOUR[i]
    st.progress((i + 1) / len(TOUR), text=f"Step {i + 1} of {len(TOUR)}")
    st.subheader(title)
    st.markdown(body)
    b1, b2, b3 = st.columns([1, 1, 1])
    if b1.button("Back", disabled=i == 0, width="stretch", key="tour_back"):
        ss.tour_step = i - 1
        st.rerun()
    if b2.button("Skip tour", width="stretch", key="tour_skip"):
        ss.tour_open = False
        st.rerun()
    last = i == len(TOUR) - 1
    if b3.button("Start" if last else "Next", type="primary", width="stretch", key="tour_next"):
        if last:
            ss.tour_open = False
        else:
            ss.tour_step = i + 1
        st.rerun()


# ---------- sidebar ----------
with st.sidebar:
    st.markdown("### LaunchGate")
    st.caption("Release blockers by market, each tied to a line of code and a dated source.")
    page = st.radio("Go to", PAGES, key="nav", label_visibility="collapsed")
    st.divider()
    if PUBLIC:
        # Public demo: visitors cannot point the server at another host.
        use_af, af_url = bool(os.environ.get("AGENTFIELD_URL")), os.environ.get("AGENTFIELD_URL", AF_DEFAULT)
        st.caption("Public demo. Checks run through the AgentField control plane. "
                   "Patch generation is turned off here; see the repo to run it yourself.")
    else:
        st.markdown("**Run settings**")
        use_af = st.toggle("Run through AgentField control plane", value=bool(os.environ.get("AGENTFIELD_URL")),
                           help="Off: the same functions run through the plain asyncio pipeline.")
        af_url = st.text_input("AgentField URL", os.environ.get("AGENTFIELD_URL", AF_DEFAULT), disabled=not use_af)
    st.divider()
    st.markdown("**Saved results**")
    if st.button("Load sample result", width="stretch", help="Hand-written example; no API calls"):
        load_result(json.loads(SAMPLE.read_text()))
        st.rerun()
    if LAST_RUN.exists() and st.button("Load last live run", width="stretch",
                                       help="Result saved by the most recent live pipeline run"):
        try:
            load_result(json.loads(LAST_RUN.read_text()), via="last_run.json (saved live run)")
            st.rerun()
        except ValueError as exc:
            st.error(f"last_run.json could not be read: {exc}")
    st.divider()
    if st.button("Replay tour", width="stretch"):
        ss.tour_open, ss.tour_step = True, 0
        st.rerun()

if ss.tour_open:
    tour()

st.markdown(
    "<div class='lg-head'><div class='name'>LaunchGate</div>"
    "<div class='line'>Release blockers by market. Each finding links a line of code to a dated source.</div>"
    "</div>", unsafe_allow_html=True)

res = ss.result
is_sample = bool(res and res.get("sample"))


def footer(dropped: int | None = None) -> None:
    extra = f" · {plural(dropped, 'candidate finding')} dropped by the evidence check." if dropped is not None else ""
    st.markdown(f"<div class='lg-foot'>Pre-counsel triage. Not legal advice.{extra}</div>", unsafe_allow_html=True)


# =====================================================================================
# New check
# =====================================================================================
if page == "New check":
    st.subheader("New launch check")
    st.caption("The repo shows what the code does. These answers tell the agents the setting it ships in.")
    with st.form("intake"):
        a, b = st.columns([3, 2])
        repo_url = a.text_input("Public git repo URL", DEMO_REPO,
                                help="Must be clonable without credentials. Python project with requirements.txt.")
        app_name = b.text_input("App name", "TripNote")
        description = st.text_area(
            "What does the app do? (one paragraph)",
            "Trip-planning web app. Users sign up with email, save a home location and trip notes, "
            "and export itineraries as PDF.", height=90)
        c1, c2 = st.columns(2)
        distribution = c1.selectbox("How is it distributed?", [
            "closed-source SaaS", "open-source", "distributed binary or on-prem", "mobile app"])
        users = c2.multiselect("Who are the users?", ["consumers", "businesses", "minors"], default=["consumers"])
        st.markdown("**Where will it launch?**")
        mcols = st.columns(len(MARKETS))
        picked = []
        for col, (m, name) in zip(mcols, MARKETS.items()):
            label = f"{name} ({m})" + ("" if m in TESTED else ", untested")
            if col.checkbox(label, value=m in TESTED, key=f"mk_{m}"):
                picked.append(m)
        d1, d2 = st.columns([3, 1])
        data_categories = d1.multiselect(
            "Personal data the app handles",
            ["email", "location", "payment", "health", "biometrics", "children's data", "none/unsure"],
            default=["email", "location"])
        uses_ai = d2.radio("Uses AI features?", ["no", "yes"], horizontal=True) == "yes"
        notes = st.text_area("Anything else the reviewers should know? (optional)", "", height=70,
                             placeholder="e.g. launch date, vendors under contract, existing certifications")
        submitted = st.form_submit_button("Run launch check", type="primary", width="stretch")

    if submitted:
        if not repo_url.strip() or not picked:
            st.error("Enter a repo URL and tick at least one market.")
        else:
            context = {"app_name": app_name.strip(), "description": description.strip(),
                       "distribution": distribution, "users": users, "data_categories": data_categories,
                       "uses_ai": uses_ai, "notes": notes.strip()}
            ss.fixes, ss.drafts, ss.log, ss.sandbox_deleted, ss.report_html = {}, {}, [], False, None
            with st.status("Running launch check", expanded=True) as status:
                def log(line: str) -> None:
                    ss.log.append(line)
                    status.write(line)
                try:
                    ss.result = run_check(repo_url.strip(), picked, use_af, af_url, log, context)
                    status.update(label="Launch check finished", state="complete", expanded=False)
                    ss["_goto"] = "Results"
                except Exception as exc:
                    ss.result = None
                    log(f"Run failed: {type(exc).__name__}: {exc}")
                    status.update(label="Launch check failed", state="error")
            if ss.result:
                st.rerun()
    elif ss.log and not res:
        st.code("\n".join(ss.log))

    with st.expander("What happens when you press Run"):
        for n, text in enumerate([
            "**Daytona** clones the repo into a sandbox and runs open-source scanners there. Nothing from the repo runs on this machine.",
            "An open model on **Nosana** reads the scanner output and key files and writes a fact sheet with file and line for every item.",
            "**Tavily** looks up vendors, vulnerabilities and licences found in the fact sheet.",
            "One agent per market × domain searches current requirements with **Tavily** and matches them to the facts.",
            "The **evidence check** drops any finding it cannot tie to a fetched source and a real line.",
        ], 1):
            st.markdown(f"<div class='lg-step'>{n}. {text}</div>", unsafe_allow_html=True)
    footer()

# =====================================================================================
# Results
# =====================================================================================
elif page == "Results":
    if not res:
        st.info("No result yet. Start on **New check**, or load saved data from the sidebar.")
        footer()
        st.stop()
    if is_sample:
        st.warning("Sample data: a hand-written example for the demo repo. No sandbox, search or model was called. "
                   "Source links in the sample are illustrative.")
    if res.get("error"):
        st.error(f"Run reported an error: {res['error']}")

    cells = res.get("cells", [])
    findings = [f for c in cells for f in c.get("findings", [])]
    n_block = sum(f["severity"] == "blocker" for f in findings)
    n_warn = sum(f["severity"] == "warning" for f in findings)
    dropped_total = res.get("dropped_total", sum(c.get("dropped_count", 0) for c in cells))
    searches = res.get("searches", {})
    ctx = res.get("context") or {}

    title = ctx.get("app_name") or "Launch check"
    completed = [c for c in cells if c.get("status") == "ok"]
    if n_block or n_warn:
        verdict = f"{plural(n_block, 'blocker')} / {plural(n_warn, 'warning')}"
    elif res.get("error") or not completed:
        verdict = "run did not complete, no checks ran"     # never report a failed run as a clean one
    else:
        verdict = "no blockers found in these checks"
    st.subheader(f"{title}: {verdict}")
    per_market = []
    for m in (res.get("markets") or sorted({c["market"] for c in cells})):
        mf = [f for c in cells if c["market"] == m for f in c.get("findings", [])]
        b = sum(f["severity"] == "blocker" for f in mf)
        w = sum(f["severity"] == "warning" for f in mf)
        if b or w:
            bits = ([plural(b, "blocker")] if b else []) + ([plural(w, "warning")] if w else [])
            per_market.append(f"{MARKETS.get(m, m)}: {', '.join(bits)}.")
        elif any(c["market"] == m and c.get("status") == "ok" for c in cells):
            per_market.append(f"{MARKETS.get(m, m)}: no blockers found in these checks.")
    if per_market:
        st.markdown(f"<div class='lg-sum'>{esc(' '.join(per_market))}</div>", unsafe_allow_html=True)
    st.caption(res.get("repo_url", ""))
    st.markdown(
        "<div class='lg-cards'>"
        f"<div class='lg-card {'red' if n_block else 'green'}'><div class='k'>Blockers</div><div class='v'>{n_block}</div></div>"
        f"<div class='lg-card {'amber' if n_warn else 'green'}'><div class='k'>Warnings</div><div class='v'>{n_warn}</div></div>"
        f"<div class='lg-card'><div class='k'>Dropped by evidence check</div><div class='v'>{dropped_total}</div></div>"
        f"<div class='lg-card'><div class='k'>Tavily searches</div><div class='v'>{searches.get('live', 0)}"
        f"<span style='font-size:.8rem;font-weight:500'> live · {searches.get('cached', 0)} cached</span></div></div>"
        f"<div class='lg-card'><div class='k'>Model host</div><div class='v small'>{esc(str(res.get('llm_host', '—')))}</div></div>"
        "</div>", unsafe_allow_html=True)

    with st.expander("Run log"):
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Daytona sandbox**  \n`{res.get('scan', {}).get('sandbox_id', '—')}`")
        c2.markdown(f"**Model**  \n`{res.get('llm_model', '—')}`")
        c3.markdown(f"**Ran via**  \n{res.get('via', 'sample file' if is_sample else '—')}")
        via_line = [f"Ran via: {res['via']}"] if res.get("via") else []
        st.code("\n".join(via_line + list(ss.log or res.get("log", []))) or "(no log lines)")

    # ---------- matrix ----------
    by_cell = {(c["market"], c["domain"]): c for c in cells}
    markets = res.get("markets") or sorted({c["market"] for c in cells})

    def cell_label(c: dict | None) -> tuple[str, str]:
        if c is None:
            return "not run", "status"
        if c.get("status", "ok") != "ok":
            return c["status"], "status"
        b = sum(f["severity"] == "blocker" for f in c["findings"])
        w = sum(f["severity"] == "warning" for f in c["findings"])
        if b:
            return plural(b, "blocker") + (f" · {plural(w, 'warning')}" if w else ""), "blocker"
        if w:
            return plural(w, "warning"), "warning"
        return "none found", "none"

    parts = ["<div class='h'></div>"] + [f"<div class='h'>{esc(MARKETS.get(m, m))}</div>" for m in markets]
    for d in DOMAINS:
        parts.append(f"<div class='r'>{d.capitalize()}</div>")
        for m in markets:
            c = by_cell.get((m, d))
            text, kind = cell_label(c)
            notes = []
            if c and c.get("cached"):
                notes.append("cached rules")
            if c and c.get("dropped_count"):
                notes.append(f"{c['dropped_count']} dropped")
            note = f"<small>{' · '.join(notes)}</small>" if notes else ""
            parts.append(f"<div class='lg-cell {kind}'>{esc(text)}{note}</div>")
    st.markdown(f"<div class='lg-matrix' style='grid-template-columns:140px repeat({len(markets)},1fr)'>"
                f"{''.join(parts)}</div>", unsafe_allow_html=True)

    # ---------- drill-down ----------
    st.subheader("Findings")
    f1, f2, f3 = st.columns([2, 2, 1])
    show_m = f1.multiselect("Market", markets, default=markets, format_func=lambda m: MARKETS.get(m, m))
    show_d = f2.multiselect("Domain", DOMAINS, default=DOMAINS, format_func=str.capitalize)
    only_block = f3.toggle("Blockers only")

    st.markdown("<div class='lg-legend'><b>From scanner</b>: reported by an open-source tool, the same on every run. "
                "<b>Model-matched</b>: proposed by the model, kept only after the evidence check.</div>",
                unsafe_allow_html=True)
    shown = 0
    for m in markets:
        market_open = False
        for d in DOMAINS:
            c = by_cell.get((m, d))
            if not c or m not in show_m or d not in show_d:
                continue
            todo = [f for f in sorted(c["findings"], key=lambda x: x["severity"] != "blocker")
                    if not (only_block and f["severity"] != "blocker")]
            if not todo:
                continue
            if not market_open:
                st.markdown(f"<div class='lg-mk'>{esc(MARKETS.get(m, m))}</div>", unsafe_allow_html=True)
                market_open = True
            st.markdown(f"<div class='lg-dm'>{d.capitalize()}</div>", unsafe_allow_html=True)
            for f in todo:
                shown += 1
                icon = "Blocker" if f["severity"] == "blocker" else "Warning"
                fid = f["id"]
                origin = f.get("origin", "model")
                with st.expander(f"{icon}: {f['title']}",
                                 expanded=fid in ss.fixes or fid in ss.drafts):
                    origin_tip = ("deterministic scanner result" if origin == "scanner"
                                  else "matched by the model, then evidence-checked")
                    st.markdown(
                        f"<span class='chip {f['severity']}'>{f['severity'].capitalize()}</span>"
                        f"<span class='chip {'scanner' if origin == 'scanner' else 'model'}' title='{origin_tip}'>"
                        f"{'From scanner' if origin == 'scanner' else 'Model-matched'}</span>"
                        f"<span class='chip plain'>{'Code fix' if f['fix_type'] == 'code' else 'Document fix'}</span>",
                        unsafe_allow_html=True)
                    st.write(f["why"])
                    left, right = st.columns(2)
                    with left:
                        st.markdown("**Evidence in the repo**")
                        ev = f["evidence"]
                        if ev.get("file") and ev.get("snippet"):
                            st.caption(f"{ev['file']}:{ev.get('line')}")
                            st.code(ev["snippet"], language="python" if ev["file"].endswith(".py") else None)
                        elif ev.get("missing_artifact"):
                            st.info(f"Missing: {ev['missing_artifact']}")
                    with right:
                        r = f["rule"]
                        st.markdown("**Rule**")
                        st.markdown(f"**{r['name']}** — {r['requirement']}")
                        tag = "cached at" if c.get("cached") else "fetched"
                        st.markdown(
                            f"<a class='pill' href='{esc(r['source_url'], quote=True)}' target='_blank' "
                            f"title='{esc(r.get('source_title') or r['source_url'], quote=True)}'>"
                            f"source · {tag} {esc(when(r.get('fetched_at')))}</a>", unsafe_allow_html=True)
                        st.caption(r.get("source_title") or r["source_url"])
                    st.markdown(f"**Fix:** {f['fix_summary']}")

                    if f["fix_type"] == "code":
                        blocked = is_sample or ss.sandbox_deleted or PUBLIC
                        if st.button("Generate patch", key=f"fix_{fid}", disabled=blocked,
                                     help=("Turned off on the public demo: the patch agent runs on the host machine"
                                           if PUBLIC else "Not available on sample data or after the sandbox is deleted")
                                     if blocked else None):
                            with st.spinner("CodeAF is writing the patch. Daytona will re-scan it."):
                                try:
                                    ss.fixes[fid] = run_patch(res, f, use_af, af_url)
                                except Exception as exc:
                                    ss.fixes[fid] = {"finding_id": fid, "diff": "", "verified": False,
                                                     "verification_log": "",
                                                     "error": f"{type(exc).__name__}: {exc}"}
                        fix = ss.fixes.get(fid)
                        if fix:
                            if fix.get("error"):
                                st.error(f"Patch step reported: {fix['error']}")
                            if fix.get("diff"):
                                st.code(fix["diff"], language="diff")
                                vlog = esc(str(fix.get("verification_log") or "no detail returned"))
                                if fix.get("verified"):
                                    st.markdown(f"<div class='lg-ok'>Patch verified in sandbox</div>",
                                                unsafe_allow_html=True)
                                    st.caption(fix.get("verification_log") or "")
                                else:
                                    st.markdown(f"<div class='lg-no'>Patch not verified: {vlog}</div>",
                                                unsafe_allow_html=True)
                                if fix.get("via"):
                                    st.caption(f"Written via {fix['via']}")
                            else:
                                st.info("No patch was produced. The fix summary above is the recommendation.")
                    else:
                        if st.button("Draft policy section", key=f"draft_{fid}", disabled=is_sample,
                                     help="Not available on sample data" if is_sample else None):
                            with st.spinner("Drafting"):
                                try:
                                    from core.match import draft_policy_section
                                    ss.drafts[fid] = draft_policy_section(f, res.get("facts", {}))
                                except Exception as exc:
                                    ss.drafts[fid] = f"Drafting failed: {type(exc).__name__}: {exc}"
                        if fid in ss.drafts:
                            st.markdown("**Draft for counsel review**")
                            st.markdown(ss.drafts[fid])
    if not shown:
        st.write("No findings for this selection.")

    # ---------- supporting data ----------
    st.subheader("Supporting data")
    scan = res.get("scan", {})
    sast = scan.get("sast") or []
    with st.expander(f"Scanner output: {len(scan.get('secrets', []))} secrets · {len(scan.get('vulns', []))} "
                     f"vulnerabilities · {len(scan.get('licences', []))} licences · {len(sast)} static-analysis hits"):
        st.caption("Deterministic open-source tools run inside the Daytona sandbox.")
        for label, key, tool in [("Secrets", "secrets", "detect-secrets"), ("Vulnerable dependencies", "vulns", "pip-audit"),
                                 ("Static analysis", "sast", "bandit"), ("Licences", "licences", "pip-licenses")]:
            rows = scan.get(key) or []
            st.markdown(f"**{label}** · `{tool}` · {len(rows)}")
            if rows:
                st.dataframe(rows, width="stretch", hide_index=True)

    enrichment = res.get("facts", {}).get("enrichment", [])
    with st.expander(f"Fact-sheet enrichment ({plural(len(enrichment), 'item')})"):
        if not enrichment:
            st.write("No enrichment items were kept.")
        for e in enrichment:
            tag = "cached at" if e.get("cached") else "fetched"
            st.markdown(
                f"<span class='chip plain'>{esc(str(e.get('kind', '')))}</span><b>{esc(str(e.get('subject', '')))}</b>"
                f"<br>{esc(str(e.get('summary', '')))}<br>"
                f"<a class='pill' href='{esc(str(e.get('source_url', '')), quote=True)}' target='_blank'>"
                f"source · {tag} {esc(when(e.get('fetched_at')))}</a>", unsafe_allow_html=True)

    if ctx:
        with st.expander("What you told us about the app"):
            st.json(ctx, expanded=True)
    with st.expander("Fact sheet (raw)"):
        st.json({k: v for k, v in res.get("facts", {}).items() if k != "enrichment"}, expanded=False)

    sandbox_id = scan.get("sandbox_id")
    if sandbox_id and not is_sample and not PUBLIC:
        if ss.sandbox_deleted:
            st.caption(f"Sandbox {sandbox_id} deleted.")
        elif st.button("Delete sandbox", help="Do this when you are done generating patches"):
            try:
                from core.sandbox import delete_sandbox
                delete_sandbox(sandbox_id)
                ss.sandbox_deleted = True
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete sandbox: {type(exc).__name__}: {exc}")
    footer(dropped_total)

# =====================================================================================
# Report
# =====================================================================================
elif page == "Report":
    st.subheader("HTML report")
    if not res:
        st.info("No result yet. Run a check or load saved data first.")
    else:
        st.write("One self-contained HTML file with the verdict, the matrix, every finding with its source, "
                 "and the scanner output. Written sections come from the model; the data tables come from the run.")
        if is_sample:
            st.caption("Sample data: the report is built without model calls.")
        if st.button("Generate HTML report", type="primary"):
            with st.spinner("Building the report"):
                try:
                    from core.report import build_report
                    ss.report_html = build_report(res, use_llm=not is_sample)
                except Exception as exc:
                    ss.report_html = None
                    st.error(f"Report could not be built: {type(exc).__name__}: {exc}")
        if ss.report_html:
            st.download_button("Download launchgate-report.html", ss.report_html,
                               file_name="launchgate-report.html", mime="text/html")
            st.components.v1.html(ss.report_html, height=900, scrolling=True)
    footer()

# =====================================================================================
# How it works
# =====================================================================================
elif page == "How it works":
    st.subheader("How it works")
    st.code("""Intake form (repo URL, app details, markets)
   │
   ▼
AgentField agent "launchgate"
   launch_check ──► scan         Daytona sandbox: clone + open-source scanners      → ScanResult
                ──► fact_sheet   Nosana model reads scan output + key files         → FactSheet
                ──► enrich       Nosana model writes queries → Tavily answers       → sourced facts
                ──► market_agent × (markets × domains), in parallel
                        rules    = Tavily search of current requirements
                        findings = Nosana model matches facts to rules
                        keep only findings that pass the evidence check
                ──► fix (on demand)  CodeAF writes a patch → Daytona re-scans it""", language=None)

    a, b = st.columns(2)
    with a:
        st.markdown("#### The evidence check")
        st.markdown(
            "The model proposes; code decides. A finding is dropped unless **all three** hold:\n"
            "1. its rule's source URL is one Tavily actually returned for that cell;\n"
            "2. its code snippet occurs within ±3 lines of the cited line in the file fetched from the sandbox, "
            "or it names a missing artifact;\n"
            "3. it states a fix.\n\n"
            "The count of dropped findings is shown on every run.")
        st.markdown("#### Deterministic vs model-written")
        st.dataframe([
            {"Step": "Secrets", "Done by": "detect-secrets", "Kind": "open-source scanner"},
            {"Step": "Vulnerable dependencies", "Done by": "pip-audit", "Kind": "open-source scanner"},
            {"Step": "Dependency licences", "Done by": "pip-licenses", "Kind": "open-source scanner"},
            {"Step": "Static analysis", "Done by": "bandit", "Kind": "open-source scanner"},
            {"Step": "Evidence check", "Done by": "core/match.py", "Kind": "plain code"},
            {"Step": "Fact sheet, rule summaries, matching", "Done by": "open model on Nosana", "Kind": "model"},
            {"Step": "Patch", "Done by": "CodeAF on the Nosana model", "Kind": "model, verified by re-scan"},
        ], width="stretch", hide_index=True)
    with b:
        st.markdown("#### What each partner technology does")
        st.dataframe([
            {"Partner": "Daytona", "Job": "Sandbox that clones the untrusted repo, installs its dependencies, runs the scanners, and re-scans after a patch"},
            {"Partner": "Tavily", "Job": "Searches current requirements per market × domain and looks up vendors, vulnerabilities and licences; every item keeps its URL and fetch time"},
            {"Partner": "Nosana", "Job": "GPU deployment serving an open model through an OpenAI-compatible endpoint; every model call goes there"},
            {"Partner": "AgentField", "Job": "Runs one reasoner per stage and fans out one agent per market × domain; shows the run trace in its dashboard"},
            {"Partner": "CodeAF", "Job": "Writes the patch for a code-fixable blocker, running on the Nosana model"},
        ], width="stretch", hide_index=True)
        st.markdown("#### Reading a finding")
        st.markdown(
            "<span class='chip blocker'>Blocker</span> should not launch in that market until fixed<br>"
            "<span class='chip warning'>Warning</span> worth a look before launch<br>"
            "<span class='chip scanner'>From scanner</span> came straight from an open-source tool<br>"
            "<span class='chip model'>Model-matched</span> proposed by the model, then evidence-checked<br>"
            "<a class='pill' href='#'>source · fetched 2026-11-14 07:10 UTC</a> opens the page the rule came from",
            unsafe_allow_html=True)
    footer()

# =====================================================================================
# About & limits
# =====================================================================================
else:
    st.subheader("About & limits")
    a, b = st.columns(2)
    with a:
        st.markdown("#### Which repos it can read")
        st.markdown(
            "- **Supported:** a public git URL that can be cloned without credentials (GitHub is the tested host), "
            "containing a Python project with a `requirements.txt`.\n"
            "- **Not supported:** private repos, repos that need a token or SSH key, and projects in other "
            "languages. Other languages will clone, but the dependency and licence scanners will find nothing.\n"
            "- Repo code is cloned and installed **only inside the Daytona sandbox**.")
        st.markdown("#### Markets and domains")
        st.markdown(
            "- **Tested:** Japan and the European Union.\n"
            "- **Selectable but untested:** United States and India.\n"
            "- **Domains:** security, privacy, licences, trade. Tax, accessibility and sector rules are out of scope.")
    with b:
        st.markdown("#### What this is, and is not")
        st.markdown(
            "- **Pre-counsel triage. Not legal advice.** It helps a team walk into a legal review with a list "
            "and the evidence behind it.\n"
            "- It never reports a product as cleared. The best verdict is "
            "*“no blockers found in these checks”*.\n"
            "- Rules come from a live web search summarised by an open model. Each carries its source link "
            "so a person can check it.")
        st.markdown("#### What is cached")
        st.markdown(
            "- Tavily responses are stored on disk in `cache/` with their fetch time, per market × domain and "
            "per enrichment subject.\n"
            "- A cell that used the cache is labelled *rules from cache*, and its sources show *cached at …*.\n"
            "- Scanner results, the fact sheet and findings are produced fresh on every run.")
        st.markdown("#### Not built")
        st.markdown("Pull-request creation, PDF export, private-repo access, and scheduled re-checks.")
    footer()
