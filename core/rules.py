"""Tavily: current requirements per market x domain, cached on disk with fetch time."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from tavily import TavilyClient

from core.llm import json_call
from core.schema import MARKET_NAMES, FactSheet, Rule, RuleDraftList

load_dotenv()

CACHE = Path(__file__).resolve().parent.parent / "cache"
SEARCHES = {"live": 0, "cached": 0}
_tv: TavilyClient | None = None

QUERY = {
    "privacy": "{market_name} personal data protection law requirements consent cross-border transfer third-party disclosure app 2026",
    "security": "{market_name} software product cybersecurity requirements vulnerability handling secrets 2026",
    "licences": "AGPL GPL obligations closed-source SaaS distribution {market_name}",
    "trade": "{market_name} export control encryption software declaration requirements 2026",
}

# Seeds from prior knowledge; the search is retried without them if it returns nothing.
OFFICIAL = {
    "JP": ["ppc.go.jp", "meti.go.jp"],
    "EU": ["eur-lex.europa.eu", "edpb.europa.eu"],
    "US": ["ftc.gov", "bis.gov"],
    "IN": ["meity.gov.in"],
}

RULES_SYS = """From the web pages given, list up to 4 requirements that are relevant to the product
facts given. Each rule needs: a short `name`, a one-sentence `requirement` stated in plain words
and supported by the page text, and the `source_url` of the page it came from, copied exactly.
Use only the pages given. If no page supports a requirement, return an empty list."""

PAGE_CHARS = 3500


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tavily() -> TavilyClient:
    global _tv
    if _tv is None:
        _tv = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tv


def cached_search(cache_path: Path, query: str, **kwargs) -> dict:
    """Returns {query, fetched_at, cached, results:[{url,title,content}]}."""
    if cache_path.exists():
        data = json.loads(cache_path.read_text())
        if data.get("query") == query and data.get("results"):
            SEARCHES["cached"] += 1
            return {**data, "cached": True}
    resp = tavily().search(query, **kwargs)
    SEARCHES["live"] += 1
    data = {
        "query": query, "fetched_at": now_iso(),
        "results": [
            {"url": r["url"], "title": r.get("title", ""),
             "content": (r.get("raw_content") or r.get("content") or "")}
            for r in resp.get("results", [])
        ],
    }
    if data["results"]:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=1))
    return {**data, "cached": False}


def _relevant_window(text: str, domain: str) -> str:
    """Raw pages are long; keep the part that mentions the domain's vocabulary."""
    words = {
        "privacy": r"consent|transfer|third part|personal (data|information)",
        "security": r"vulnerab|secret|credential|security requirement",
        "licences": r"AGPL|Affero|network|source code|copyleft",
        "trade": r"encrypt|cryptograph|export|dual-use",
    }[domain]
    m = re.search(words, text, flags=re.I)
    start = max(0, (m.start() if m else 0) - 500)
    return text[start:start + PAGE_CHARS]


def _extract_rules(market: str, domain: str, facts: FactSheet, res: dict) -> list[Rule]:
    pages = {r["url"]: r for r in res["results"]}
    page_text = "\n\n".join(
        f"--- PAGE {r['url']} | {r['title']} ---\n{_relevant_window(r['content'], domain)}"
        for r in res["results"]
    )
    relevant = facts.model_dump(include={
        "personal_data", "third_parties", "consent_points", "crypto", "policy_declares",
        "secrets", "vulns", "distribution", "product_context"})
    relevant["non_permissive_licences"] = [
        l for l in facts.licences if re.search(r"GPL|Affero|SSPL", str(l.get("licence")), re.I)]
    user = f"Market: {MARKET_NAMES[market]}\nDomain: {domain}\n\nProduct facts:\n{json.dumps(relevant)[:5000]}\n\nPages:\n{page_text}"
    drafts = json_call(RULES_SYS, user, RuleDraftList)

    by_norm = {u.rstrip("/"): u for u in pages}             # tolerate a trailing-slash difference only
    rules = []
    for d in drafts.rules:
        url = by_norm.get(d.source_url.strip().rstrip("/"))
        if url is None:                                      # reject rules citing a URL we did not fetch
            continue
        rules.append(Rule(market=market, domain=domain, name=d.name, requirement=d.requirement,
                          source_url=url, source_title=pages[url]["title"],
                          fetched_at=res["fetched_at"]))
    return rules[:4]


def fetch_rules(market: str, domain: str, facts: FactSheet) -> dict:
    """Returns {rules:[Rule], urls:[str], cached:bool, fetched_at:str, error:str|None}."""
    q = QUERY[domain].format(market_name=MARKET_NAMES[market])
    opts = dict(search_depth="advanced", max_results=5, include_raw_content="markdown")
    # licence obligations are not published on regulators' sites, so that domain searches the open web
    official = None if domain == "licences" else OFFICIAL.get(market)
    res = cached_search(CACHE / "rules" / f"{market}_{domain}.json", q, include_domains=official, **opts)
    rules = _extract_rules(market, domain, facts, res) if res["results"] else []
    if not rules and official:                               # official sites gave nothing usable
        res = cached_search(CACHE / "rules" / f"{market}_{domain}_open.json", q, **opts)
        rules = _extract_rules(market, domain, facts, res) if res["results"] else []
    return {"rules": rules, "urls": [r["url"] for r in res["results"]], "cached": res["cached"],
            "fetched_at": res["fetched_at"], "error": None if rules else "no rules retrieved"}
