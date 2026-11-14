"""Tavily enrichment of the fact sheet. The Nosana model writes the queries and the summaries;
an enrichment is kept only if it cites a URL that Tavily actually returned."""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from core.llm import json_call
from core.rules import CACHE, cached_search
from core.schema import Enrichment, FactSheet, QueryList

MAX_SUBJECTS = 8                                             # hard cap for latency
NON_PERMISSIVE = re.compile(r"GPL|Affero|SSPL|EUPL|Commons Clause", re.I)

DEFAULT_QUERY = {
    "vendor": "{} data processing location data residency data processing agreement",
    "vuln": "{} severity impact fixed version",
    "licence": "{} licence obligations closed-source SaaS network use",
}

QUERY_SYS = """Write one web search query per subject, in the same order, keeping each `subject` unchanged.
- kind "vendor": find where the vendor processes and stores customer data and whether it publishes a
  data processing agreement.
- kind "vuln": find the severity of the vulnerability, what it allows, and the fixed version.
- kind "licence": find the obligations of that licence for a closed-source hosted (SaaS) product.
Queries are short keyword phrases, not sentences."""

SUMMARY_SYS = """Summarise what the search results say about the subject in at most two sentences.
- vendor: where it processes/stores data; whether a data processing agreement is published.
- vuln: severity, what it allows, fixed version.
- licence: obligations for a closed-source hosted product.
State only what the results say. If they do not say, write "not stated in the sources".
`source_url` must be copied exactly from one of the results. Keep `subject` and `kind` unchanged."""


def pick_subjects(facts: FactSheet) -> list[dict]:
    subjects: list[dict] = []
    for name in dict.fromkeys(t.get("name") for t in facts.third_parties if t.get("name")):
        subjects.append({"subject": str(name), "kind": "vendor"})
    for v in facts.vulns:
        subjects.append({"subject": f"{v['vuln_id']} {v['package']} {v['version']}", "kind": "vuln"})
    for l in facts.licences:
        if NON_PERMISSIVE.search(str(l.get("licence"))):
            subjects.append({"subject": f"{l['package']} ({l['licence']})", "kind": "licence"})
    # one of each kind first, so the cap never removes a whole kind
    seen: dict[str, int] = {}
    ranks = []
    for s in subjects:
        ranks.append(seen.get(s["kind"], 0))
        seen[s["kind"]] = ranks[-1] + 1
    order = sorted(range(len(subjects)), key=lambda i: ranks[i])     # stable within a rank
    return [subjects[i] for i in order][:MAX_SUBJECTS]


def _one(subject: dict, query: str) -> dict | None:
    slug = re.sub(r"[^a-z0-9]+", "_", subject["subject"].lower()).strip("_")[:60]
    res = cached_search(CACHE / "enrich" / f"{subject['kind']}_{slug}.json", query,
                        search_depth="basic", max_results=3)
    hits = [{"url": r["url"], "title": r["title"], "content": r["content"][:1500]} for r in res["results"]]
    if not hits:
        return None
    try:
        e = json_call(SUMMARY_SYS, json.dumps({"subject": subject, "results": hits}), Enrichment)
    except ValueError:
        return None
    fetched = {h["url"].rstrip("/"): h["url"] for h in hits}
    url = fetched.get(e.source_url.strip().rstrip("/"))      # same evidence rule as findings
    if url is None:
        return None
    return {**e.model_dump(), "source_url": url, "subject": subject["subject"], "kind": subject["kind"],
            "query": query, "fetched_at": res["fetched_at"], "cached": res["cached"]}


def enrich(facts: FactSheet) -> FactSheet:
    subjects = pick_subjects(facts)
    if not subjects:
        return facts
    try:
        written = [q.query for q in json_call(QUERY_SYS, json.dumps(subjects), QueryList).items]
    except ValueError:
        written = []
    # the model may return too few queries, or none; fall back to a template for the rest
    queries = [
        written[i] if i < len(written) and written[i].strip() else DEFAULT_QUERY[s["kind"]].format(s["subject"])
        for i, s in enumerate(subjects)
    ]
    pairs = list(zip(subjects, queries))
    with ThreadPoolExecutor(max_workers=8) as pool:          # Tavily lookups run concurrently
        results = list(pool.map(lambda p: _one(*p), pairs))
    facts.enrichment = [r for r in results if r]
    return facts
