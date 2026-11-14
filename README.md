# LaunchGate

Give it a public repo and target markets; get a market × domain matrix of release blockers.
Every finding links a line of code (or a named missing document) to a dated web source. One blocker gets an automatic patch.

> Pre-counsel triage. Not legal advice. Verdicts read "N blockers / N warnings / no blockers found in these checks".

<!-- TODO before submitting: add a screenshot of the matrix from a live run -->

The UI asks about the app (what it does, how it is distributed, who uses it, which personal data it handles, where it will launch), runs the check, shows the matrix and findings, generates a patch for code-fixable blockers, drafts a policy section for document fixes, and exports an HTML report.

## Partner tech usage

| Sponsor | File | Function | What it does |
|---|---|---|---|
| **Daytona** | `core/sandbox.py` | `scan_repo`, `verify_fix`, `delete_sandbox` | Clones the untrusted repo in a sandbox, installs its dependencies there, runs `detect-secrets`, `pip-audit`, `pip-licenses`, `bandit`; re-scans after the patch. The sandbox ID is in the run log. Repo code never runs on the host. |
| **Tavily** | `core/rules.py` | `fetch_rules`, `cached_search` | Searches current requirements per market × domain. URL and fetch time are stored on every rule. |
| **Tavily** | `core/enrich.py` | `enrich` | Looks up vendor data location, vulnerability details and licence obligations for items in the fact sheet. The Nosana model writes the queries. |
| **Nosana** | `scripts/nosana_deploy.py`, `core/llm.py` | `deploy`, `json_call`, `text_call` | A GPU deployment serves an open model through an OpenAI-compatible endpoint. Every model call goes there: fact sheet, query writing, rule extraction, matching, policy drafting, and CodeAF. The endpoint host is in the run log. |
| **AgentField** | `app.py` | reasoners `scan`, `fact_sheet`, `enrich`, `market_agent`, `launch_check`, `fix` | One reasoner per pipeline stage; `launch_check` fans out one `market_agent` per market × domain with `asyncio.gather`; the run trace is in the AgentField dashboard. |
| **Nosana + AgentField** | `core/report.py`, `app.py` | `build_report`, reasoner `report` | Three writer agents, each with its own format prompt, write the report's summary, market briefs and remediation plan; tables come from the run data. |
| **CodeAF** | `app.py`, `core/fix.py` | `fix` (via `app.harness`), `run_codeaf_cli` | Writes the patch for a code-fixable blocker on a local clone, running on the Nosana model through `CODEAF_BASE_URL`. Daytona then verifies the diff. |

## Architecture

```
Streamlit UI (ui.py)
   │  repo_url, markets
   ▼
AgentField agent "launchgate" (app.py)
   launch_check ──► scan         (Daytona: clone + scanners)          → ScanResult
                ──► fact_sheet   (Nosana LLM reads scan + key files)   → FactSheet
                ──► enrich       (Nosana LLM writes queries → Tavily)  → FactSheet.enrichment
                ──► market_agent × (markets × domains), in parallel
                        rules  = Tavily search            → [Rule]
                        finds  = Nosana LLM match         → [Finding]
                        keep only findings passing the evidence check
                ──► fix (on demand): CodeAF on the Nosana model → Daytona re-scan → Fix
```

All logic is in plain functions under `core/`. `app.py` only wraps them as reasoners.
`core/pipeline.py` runs the same functions with `asyncio.gather` when the control plane is not running.

### The evidence check (`core/match.py`, `evidence_ok`)

The model proposes findings; code decides which survive. A finding is dropped unless:

1. its rule's source URL is one Tavily returned for that cell;
2. its snippet occurs within ±3 lines of the cited line in the file fetched from the sandbox, or it names a missing artifact;
3. it states a fix.

The UI shows how many were dropped.

## Status (14 Nov 2026)

| | State |
|---|---|
| **Daytona** | Run live. Each check creates a sandbox, clones the repo and runs four open-source scanners in about 22 s. Sandbox ID is in the run log. |
| **Tavily** | Run live. Rules for all 8 Japan/EU cells and 4 fact-sheet enrichments; responses cached in `cache/` with fetch time. |
| **Nosana** | Run live. `scripts/nosana_deploy.py` deployed `cyankiwi/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit` on vLLM v0.10.2 (RTX 3090 market); cold start about 10 minutes. Every model call in the run log is served by the `*.node.k8s.prd.nos.ci` endpoint. |
| **CodeAF** | Run live on the Nosana model through `CODEAF_BASE_URL`, both through AgentField's `app.harness` (reasoner `fix`) and through `codeaf exec`. It patched the hard-coded key in `config.py` in 28–60 s. A patch counts as verified only if the Daytona re-scan is clean and the repo's own tests pass in the sandbox; a rejected patch is retried once with the verifier's reason in the prompt. |
| **AgentField** | Run live with control plane v0.1.145. The `launchgate` node registers 7 reasoners; `launch_check` ran through `POST /api/v1/execute/async/launchgate.launch_check` in 52–56 s, fanning out 8 `market_agent` calls; `fix` ran CodeAF through `app.harness` in 32 s. Runs are visible in the dashboard at `/ui/`. |
| Full run, Japan + EU | 8 cells in 52–56 s on the demo repo. Counts vary between runs for model-matched findings (9–11 blockers, 4–6 warnings); the scanner findings are the same every run. |
| HTML report | Run live: three writer agents (summary, market brief, remediation plan), 7–19 s. |
| Offline tests | 85 pass (`pytest tests`), model and Tavily replaced by fakes. |
| Not implemented | Private repos, non-Python projects, PR creation, PDF export. US and India are selectable but untested. |

### Which results are deterministic

| Check | Tool | Model involved? |
|---|---|---|
| Hard-coded secrets | `detect-secrets` | No |
| Known-vulnerable dependencies | `pip-audit` | No |
| Dependency licences | `pip-licenses` | No |
| Insecure code patterns | `bandit` | No |
| Personal data, third parties, consent, crypto use | Nosana model reads the files | Yes, then filtered: file must exist in the repo |
| Market rules | Tavily search, summarised by the Nosana model | Yes, then filtered: source URL must be one Tavily returned |
| Matching facts to rules | Nosana model | Yes, then the evidence check |

Scanner results become findings in code (`scanner_findings` in `core/match.py`) and are marked `origin: scanner`; model-matched findings are marked `origin: model`.

### What it can scan

Any git URL that can be cloned without credentials (public GitHub tested; public GitLab or Bitbucket should work the same way but are untested). The project must be Python with a `requirements.txt` at the repo root. Private repos, monorepos with nested requirements, `pyproject.toml`-only projects and other languages are not supported.

### Known weak spots

- A fictional vendor name in the demo repo ("MetricFlow") collides with a real product, so its vendor enrichment cites that product's docs. The source link makes this visible; a real vendor name would not have the problem.
- The trade column returned no findings on the demo repo even though it uses AES. No rule the model extracted matched.
- Model-written findings vary slightly between runs; scanner findings do not.

## How to run

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in DAYTONA_API_KEY, TAVILY_API_KEY, NOSANA_API_KEY
.venv/bin/python scripts/nosana_deploy.py deploy    # prints LLM_BASE_URL for .env
```

Without the control plane (plain pipeline):

```bash
.venv/bin/python -m core.pipeline https://github.com/<you>/launchgate-demo-repo JP,EU
.venv/bin/streamlit run ui.py   # untick "Run through AgentField control plane"
```

With AgentField:

```bash
# install the control plane without the coding-agent skill and macOS tray
curl -fsSL https://agentfield.ai/install.sh | bash -s -- --no-skill --no-tray
AGENTFIELD_AGENT_CALL_TIMEOUT=900s af server --port 8085 --open=false   # dashboard: http://localhost:8085/ui/
PORT=8001 .venv/bin/python app.py   # set AGENTFIELD_URL=http://localhost:8085 in .env first
.venv/bin/python app.py         # registers the "launchgate" node
.venv/bin/streamlit run ui.py
```

The UI calls `POST /api/v1/execute/async/launchgate.launch_check` and polls
`GET /api/v1/executions/{id}`, because a full run outlasts the 90-second default of the sync call.

Nosana notes: the deploy script defaults to the RTX 3090 market (24 GB) and vLLM image `v0.10.2`,
which is cached on the 24 GB markets. CodeAF needs native tool calls, so the model and
`--tool-call-parser` must match (`qwen3_coder` for the default Qwen3-Coder quant, `hermes` for
Qwen2.5-Coder). Stop the deployment when done: `scripts/nosana_deploy.py stop <id>`.
What was checked against sponsor docs, and what could not be, is in `docs/VERIFIED.md`.

Tests: `.venv/bin/python -m pytest tests -q`

## Roadmap

- Dependency-upgrade patches, with the release note that justifies each change cited.
- Pull-request delivery instead of a diff.
- More ecosystems (JavaScript/TypeScript first), private repos, more markets.

Pitch: `pitch/pitch.html` (4 slides, self-contained).

## Limits

- Pre-counsel triage, not legal advice. It never reports a product as cleared.
- Two markets (Japan, EU) and four domains (security, privacy, licences, trade).
- Rules come from a live web search summarised by a small open model; each one carries its source link so a person can check it.
- Public GitHub Python repos with a `requirements.txt` only.
