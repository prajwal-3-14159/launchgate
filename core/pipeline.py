"""Plain-asyncio orchestrator. Same functions the AgentField reasoners wrap, so the demo
still runs if the control plane does not."""
import asyncio
from typing import Callable

from core import llm
from core.enrich import enrich
from core.facts import build_fact_sheet
from core.match import match_cell
from core.rules import SEARCHES
from core.sandbox import scan_repo
from core.schema import DOMAINS, FactSheet, ScanResult


def scan_line(scan: ScanResult) -> str:
    return (f"Daytona sandbox {scan.sandbox_id}: {len(scan.files)} files, {len(scan.secrets)} secrets, "
            f"{len(scan.vulns)} vulnerabilities, {len(scan.sast)} static-analysis issues, {len(scan.licences)} licences")


def apply_context(facts: dict, context: dict | None) -> dict:
    """Answers from the intake form override what was guessed from the repo."""
    if context:
        facts["product_context"] = context
        if context.get("distribution"):
            facts["distribution"] = context["distribution"]
    return facts


def assemble(repo_url: str, markets: list[str], scan: dict, facts: dict, cells: list[dict],
             log: list[str], error: str | None = None, context: dict | None = None) -> dict:
    blockers = sum(1 for c in cells for f in c["findings"] if f["severity"] == "blocker")
    warnings = sum(1 for c in cells for f in c["findings"] if f["severity"] == "warning")
    dropped = sum(c["dropped_count"] for c in cells)
    if cells:
        log.append(f"{len(cells)} cells: {blockers} blockers, {warnings} warnings, "
                   f"{dropped} findings dropped by the evidence check")
        log.append(f"Tavily searches: {SEARCHES['live']} live, {SEARCHES['cached']} from cache; "
                   f"model calls: {llm.CALLS['count']} on {llm.host()}")
    return {"repo_url": repo_url, "markets": markets, "context": context or {}, "scan": scan, "facts": facts, "cells": cells,
            "log": log, "llm_host": llm.host(), "llm_model": llm.model(),
            "searches": dict(SEARCHES), "dropped_total": dropped, "error": error}


async def launch_check_async(repo_url: str, markets: list[str],
                             log: Callable[[str], None] | None = None,
                             context: dict | None = None) -> dict:
    lines: list[str] = []

    def say(msg: str) -> None:
        lines.append(msg)
        if log:
            log(msg)

    scan_d, facts_d = {}, {}
    try:
        say(f"Scanning {repo_url} in a Daytona sandbox…")
        scan = await asyncio.to_thread(scan_repo, repo_url)
        scan_d = scan.model_dump()
        say(scan_line(scan))

        say(f"Building fact sheet with {llm.model()} on {llm.host()}…")
        facts = await asyncio.to_thread(build_fact_sheet, scan)
        facts = FactSheet(**apply_context(facts.model_dump(), context))
        say(f"Fact sheet: {len(facts.personal_data)} personal-data items, "
            f"{len(facts.third_parties)} third parties, {len(facts.consent_points)} consent points")

        try:
            facts = await asyncio.to_thread(enrich, facts)
            say(f"Enrichment: {len(facts.enrichment)} sourced facts from Tavily")
        except Exception as exc:                              # enrichment is optional
            say(f"Enrichment skipped: {type(exc).__name__}: {str(exc)[:200]}")
        facts_d = facts.model_dump()

        say(f"Running {len(markets) * len(DOMAINS)} market × domain agents in parallel…")
        cells = await asyncio.gather(*[
            asyncio.to_thread(match_cell, m, d, facts, scan) for m in markets for d in DOMAINS])
        return assemble(repo_url, markets, scan_d, facts_d, list(cells), lines, context=context)
    except Exception as exc:
        say(f"Run failed: {type(exc).__name__}: {str(exc)[:400]}")
        return assemble(repo_url, markets, scan_d, facts_d, [], lines,
                        error=f"{type(exc).__name__}: {exc}", context=context)


def run_launch_check(repo_url: str, markets: list[str],
                     log: Callable[[str], None] | None = None, context: dict | None = None) -> dict:
    return asyncio.run(launch_check_async(repo_url, markets, log, context))


def run_fix(repo_url: str, sandbox_id: str, finding: dict) -> dict:
    """Direct path (no control plane): CodeAF CLI on the Nosana model, then Daytona re-scan."""
    from core.fix import patch_with_cli
    try:
        return patch_with_cli(repo_url, sandbox_id, finding)
    except Exception as exc:
        return {"finding_id": finding.get("id"), "diff": "", "verified": False,
                "verification_log": "", "error": f"{type(exc).__name__}: {exc}", "via": "codeaf exec (CLI)"}


if __name__ == "__main__":
    import json
    import sys
    out = run_launch_check(sys.argv[1], sys.argv[2].split(",") if len(sys.argv) > 2 else ["JP", "EU"], print)
    print(json.dumps({"cells": [(c["market"], c["domain"], c["status"], len(c["findings"]),
                                 c["dropped_count"]) for c in out["cells"]]}, indent=1))
    json.dump(out, open("last_run.json", "w"), indent=1)
