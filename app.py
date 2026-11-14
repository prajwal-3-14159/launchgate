"""AgentField agent "launchgate": one reasoner per pipeline stage, fan-out of market x domain
agents, and the CodeAF fix step. Start the control plane first (`af server`), then `python app.py`.
All logic lives in core/; this file only wraps it."""
import asyncio
import os

from agentfield import Agent, HarnessConfig
from dotenv import load_dotenv

from core import fix as fixmod
from core import llm
from core.enrich import enrich as enrich_facts
from core.facts import build_fact_sheet
from core.match import match_cell
from core.pipeline import apply_context, assemble, scan_line
from core.sandbox import scan_repo
from core.schema import DOMAINS, FactSheet, ScanResult

load_dotenv()
if os.environ.get("LLM_BASE_URL"):                           # CodeAF reads its endpoint from the environment
    os.environ.update({k: v for k, v in fixmod.codeaf_env().items() if k != "OPENROUTER_API_KEY"})
    os.environ.pop("OPENROUTER_API_KEY", None)

app = Agent(
    node_id="launchgate",
    agentfield_server=os.environ.get("AGENTFIELD_URL", "http://localhost:8080"),
    harness_config=HarnessConfig(cwd=str(fixmod.WORKDIR), max_turns=15),   # CodeAF is the default harness
)
ME = app.node_id


@app.reasoner()
async def scan(repo_url: str) -> dict:
    """Daytona: clone the repo in a sandbox and run the three scanners."""
    return (await asyncio.to_thread(scan_repo, repo_url)).model_dump()


@app.reasoner()
async def fact_sheet(scan: dict) -> dict:
    """Nosana model: scan output + key files -> fact sheet."""
    return (await asyncio.to_thread(build_fact_sheet, ScanResult(**scan))).model_dump()


@app.reasoner()
async def enrich(facts: dict) -> dict:
    """Nosana model writes queries; Tavily answers; sourced facts are added to the sheet."""
    return (await asyncio.to_thread(enrich_facts, FactSheet(**facts))).model_dump()


@app.reasoner()
async def market_agent(market: str, domain: str, facts: dict, scan: dict) -> dict:
    """One matrix cell: Tavily rules -> Nosana match -> evidence check."""
    return await asyncio.to_thread(match_cell, market, domain, FactSheet(**facts), ScanResult(**scan))


@app.reasoner()
async def launch_check(repo_url: str, markets: list[str], context: dict | None = None) -> dict:
    log = [f"AgentField run on node {ME}"]
    s = await app.call(f"{ME}.scan", repo_url=repo_url)
    log.append(scan_line(ScanResult(**s)))
    f = apply_context(await app.call(f"{ME}.fact_sheet", scan=s), context)
    try:
        f = await app.call(f"{ME}.enrich", facts=f)
        log.append(f"Enrichment: {len(f.get('enrichment', []))} sourced facts from Tavily")
    except Exception as exc:
        log.append(f"Enrichment skipped: {type(exc).__name__}")
    cells = await asyncio.gather(*[
        app.call(f"{ME}.market_agent", market=m, domain=d, facts=f, scan=s)
        for m in markets for d in DOMAINS])
    return assemble(repo_url, markets, s, f, list(cells), log, context=context)


@app.reasoner()
async def fix(repo_url: str, sandbox_id: str, finding: dict) -> dict:
    """CodeAF (on the Nosana model) patches a local clone; Daytona re-scans and runs the repo's tests.
    A rejected patch is retried once with the verifier's reason in the prompt."""
    out, feedback = {}, None
    for attempt in (1, 2):
        await asyncio.to_thread(fixmod.prepare_workdir, repo_url)
        prompt = fixmod.fix_prompt(finding, feedback)
        via, error = f"app.harness → CodeAF on {llm.host()}, attempt {attempt}", None
        try:
            result = await app.harness(prompt, model=llm.model(), env=fixmod.codeaf_env(),
                                       cwd=str(fixmod.WORKDIR), max_turns=15)
            if result.is_error:
                error = result.error_message
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if error and not fixmod.collect_diff().strip():       # fallback: CodeAF CLI directly
            ok, cli_out = await asyncio.to_thread(fixmod.run_codeaf_cli, prompt)
            via = f"codeaf exec (CLI fallback) on {llm.host()}, attempt {attempt}"
            error = None if ok else f"{error}; CLI: {cli_out[-400:]}"
        out = await asyncio.to_thread(fixmod.finish, finding, sandbox_id, via, error)
        if out["verified"]:
            break
        feedback = out["verification_log"] or out.get("error") or "no patch produced"
    return out


@app.reasoner()
async def report(run: dict) -> dict:
    """Writer agents (Nosana model, one format prompt each) produce the HTML report."""
    from core.report import build_report
    return {"html": await asyncio.to_thread(build_report, run)}


if __name__ == "__main__":
    app.run()
