"""FactSheet x rules -> findings, then the evidence check. The check is enforced in code:
a finding the model cannot tie to a fetched source and to a real line (or a named missing
artifact) is dropped, and the number dropped is reported."""
import hashlib
import json
import re

from core.llm import json_call, text_call
from core.rules import fetch_rules
from core.schema import MARKET_NAMES, FactSheet, Finding, FindingDraftList, Rule, ScanResult

MATCH_SYS = """You compare facts about a software product with requirements for one market and domain.
List the release issues: places where a fact conflicts with, or fails to meet, a rule.

For each finding:
- title: REQUIRED. A short headline of the issue, under 12 words (e.g. "Email sent to US analytics vendor, not disclosed").
- rule_index: index of the rule in the `rules` list that it is based on.
- severity: "blocker" if the product should not launch in this market until fixed, else "warning".
- evidence: either {file, line, snippet} where snippet is ONE line copied character-for-character
  from the fact sheet's file at that line; or {missing_artifact} naming what is absent
  (e.g. "no cross-border transfer disclosure in PRIVACY.md"). Use file/line from the facts only.
- fix_type: "code" if a code or dependency change fixes it, "document" if a policy/notice change does.
- fix_summary: one sentence saying what to change.
- why: one or two sentences linking the fact to the rule. If you rely on an `enrichment` item,
  quote its source_url in `why`.
Report only issues that belong to the given domain, and do not repeat issues from another domain:
security = secrets, vulnerabilities, insecure code; privacy = personal data, consent, cross-border
transfers, disclosure; licences = open-source licence obligations; trade = export controls on encryption.
Items in `already_reported` are covered by scanners; do not report them again.
Report only issues supported by both a fact and a rule. If there are none, return an empty list.
Never say the product is compliant."""

DRAFT_SYS = """You draft one section of a privacy policy in plain English for a product team to hand
to counsel. Write only the section (a heading and 3-6 sentences). State facts from the input only;
mark anything unknown as [TO CONFIRM]. This is a draft for legal review, not legal advice."""

DOMAIN_FACTS = {
    "security": {"secrets", "vulns", "crypto"},
    "privacy": {"personal_data", "third_parties", "consent_points", "policy_declares", "ai_features"},
    "licences": {"licences", "distribution"},
    "trade": {"crypto", "distribution"},
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def evidence_ok(f, scan: ScanResult, cell_urls: set[str], rule: Rule) -> bool:
    if rule.source_url not in cell_urls:                     # 1. source was fetched for this cell
        return False
    if not f.fix_summary.strip():                            # 3. a fix is stated
        return False
    ev = f.evidence
    if ev.file and ev.file not in scan.files:                # a cited file must exist in the repo
        return False
    if ev.snippet or ev.line:                                # 2a. a claimed line must verify
        if not (ev.file and ev.line and ev.line > 0 and ev.snippet and ev.file in scan.key_files):
            return False
        lines = scan.key_files[ev.file].splitlines()         # snippet within +/-3 lines of `line`
        window = _norm(" ".join(lines[max(0, ev.line - 4):ev.line + 3]))
        first = _norm(next((l for l in ev.snippet.splitlines() if l.strip()), ""))
        first = re.sub(r"^\d+\s*[:|]?\s+", "", first) if first not in window else first  # copied line-number prefix
        return len(first) >= 4 and first in window
    return bool(ev.missing_artifact)                         # 2b. a named missing artifact


ENRICH_KIND = {"security": "vuln", "privacy": "vendor", "licences": "licence", "trade": None}
NON_PERMISSIVE = re.compile(r"GPL|Affero|SSPL|EUPL", re.I)
SCANNER_SOURCE = {
    "secret": ("detect-secrets", "https://github.com/Yelp/detect-secrets"),
    "sast": ("Bandit", "https://bandit.readthedocs.io/en/latest/"),
    "licence": ("GNU Affero General Public License v3", "https://www.gnu.org/licenses/agpl-3.0.html"),
}


def _pick_rule(rules: list[Rule], words: str, market: str, domain: str, fallback: tuple[str, str]) -> Rule:
    """Best-matching market rule by keyword; if the cell has no rules, cite the scanner's own source."""
    scored = [(len(re.findall(words, f"{r.name} {r.requirement}", re.I)), -i, r) for i, r in enumerate(rules)]
    if scored:
        return max(scored, key=lambda t: t[:2])[2]
    from core.rules import now_iso
    return Rule(market=market, domain=domain, name=f"Scanner result ({fallback[0]})",
                requirement="Reported by an open-source scanner. No market-specific rule was retrieved for this cell.",
                source_url=fallback[1], source_title=fallback[0], fetched_at=now_iso())


def _req_line(scan: ScanResult, package: str) -> tuple[int | None, str | None]:
    want = re.sub(r"[-_.]+", "-", package).lower()
    for n, line in enumerate(scan.key_files.get("requirements.txt", "").splitlines(), 1):
        name = re.split(r"[=<>~!\[; ]", line.strip(), maxsplit=1)[0]
        if name and re.sub(r"[-_.]+", "-", name).lower() == want:
            return n, line.strip()
    return None, None


def scanner_findings(market: str, domain: str, facts: FactSheet, scan: ScanResult, rules: list[Rule]) -> list[dict]:
    """Deterministic findings straight from the open-source scanners. No model involved."""
    out: list[dict] = []

    def add(severity, title, why, file, line, snippet, rule, fix_type, fix):
        if not (file and line and snippet):
            return
        key = f"{market}|{domain}|scanner|{title}|{file}|{line}"
        out.append(Finding(
            id=hashlib.sha1(key.encode()).hexdigest()[:10], market=market, domain=domain, severity=severity,
            title=title, why=why, evidence={"file": file, "line": line, "snippet": snippet}, rule=rule,
            fix_type=fix_type, fix_summary=fix, origin="scanner").model_dump())

    def src_line(file, line):
        lines = scan.key_files.get(file, "").splitlines()
        return lines[line - 1].strip() if line and 0 < line <= len(lines) else None

    if domain == "security":
        for s in scan.secrets:
            add("blocker", f"Hard-coded secret in {s['file']}",
                f"detect-secrets flagged a {s.get('type')} at this line. A credential committed to the repository is exposed to everyone with access to it.",
                s["file"], s.get("line"), src_line(s["file"], s.get("line")),
                _pick_rule(rules, r"secret|credential|key|access control|protect", market, domain, SCANNER_SOURCE["secret"]),
                "code", "Read the value from an environment variable, add the name to .env.example, and rotate the exposed credential.")
        by_pkg: dict[str, list[dict]] = {}
        for v in scan.vulns:
            by_pkg.setdefault(v["package"], []).append(v)
        for pkg, vs in by_pkg.items():
            line, snippet = _req_line(scan, pkg)
            ids = ", ".join(v["vuln_id"] for v in vs)
            fixes = sorted({f for v in vs for f in v.get("fix_versions", [])},
                           key=lambda x: [int(n) for n in re.findall(r"\d+", x)])
            add("blocker", f"{pkg} {vs[0]['version']} has known vulnerabilities ({ids})",
                f"pip-audit reports {len(vs)} published advisories for this pinned version: {ids}.",
                "requirements.txt", line, snippet,
                _pick_rule(rules, r"vulnerab|patch|update|component|supply|SBOM", market, domain,
                           (f"OSV advisory {vs[0]['vuln_id']}", f"https://osv.dev/vulnerability/{vs[0]['vuln_id']}")),
                "code", f"Upgrade {pkg} to {fixes[-1] if fixes else 'a fixed release'} or later in requirements.txt.")
        for b in scan.sast:
            add("warning", f"{b.get('issue')} ({b.get('test_id')})",
                f"Bandit static analysis, severity {b.get('severity')}.",
                b["file"], b.get("line"), src_line(b["file"], b.get("line")),
                _pick_rule(rules, r"secure|vulnerab|design|development|code", market, domain, SCANNER_SOURCE["sast"]),
                "code", "Replace the flagged call with its safe equivalent.")
    if domain == "licences" and "closed" in facts.distribution.lower():
        for l in scan.licences:
            if NON_PERMISSIVE.search(str(l.get("licence"))):
                line, snippet = _req_line(scan, l["package"])
                add("warning", f"{l['package']} is licensed under {str(l['licence'])[:60]}",
                    f"pip-licenses reports this licence for a dependency of a product declared as {facts.distribution}.",
                    "requirements.txt", line, snippet,
                    _pick_rule(rules, r"AGPL|Affero|GPL|copyleft|source code|network", market, domain, SCANNER_SOURCE["licence"]),
                    "code", f"Replace {l['package']} with a permissively licensed library, buy a commercial licence, or publish the source as the licence requires.")
    return out


def match_cell(market: str, domain: str, facts: FactSheet, scan: ScanResult) -> dict:
    """One matrix cell. Never raises: failures become a status the UI shows."""
    cell = {"market": market, "domain": domain, "findings": [], "dropped_count": 0,
            "rules": [], "cached": False, "fetched_at": None, "status": "ok"}
    try:
        got = fetch_rules(market, domain, facts)
    except ValueError:                                       # model returned invalid JSON twice
        return {**cell, "status": "analysis failed"}
    except Exception as exc:
        return {**cell, "status": f"rule search failed: {type(exc).__name__}: {str(exc)[:200]}"}
    cell.update(rules=[r.model_dump() for r in got["rules"]], cached=got["cached"],
                fetched_at=got["fetched_at"])
    cell["findings"] = scanner_findings(market, domain, facts, scan, got["rules"])
    if not got["rules"]:
        if cell["findings"]:
            return {**cell, "note": "scanner findings only; no market rules retrieved"}
        return {**cell, "status": "no rules retrieved"}

    view = facts.model_dump(include=DOMAIN_FACTS[domain])
    if domain == "licences":
        view["licences"] = [l for l in facts.licences
                            if re.search(r"GPL|Affero|SSPL|EUPL", str(l.get("licence")), re.I)]
        view["dependency_file"] = "requirements.txt"
    kind = ENRICH_KIND[domain]
    view["enrichment"] = [e for e in facts.enrichment if e.get("kind") == kind]
    view["product_context"] = facts.product_context
    snippets = {
        path: {n + 1: line for n, line in enumerate(text.splitlines()) if line.strip()}
        for path, text in scan.key_files.items()
        if path in json.dumps(view) or (path == "requirements.txt" and domain in ("security", "licences"))
    }
    user = json.dumps({
        "market": MARKET_NAMES[market], "domain": domain,
        "rules": [{"index": i, "name": r.name, "requirement": r.requirement}
                  for i, r in enumerate(got["rules"])],
        "facts": view, "file_lines": snippets,
        "already_reported": [f["title"] for f in cell["findings"]],
    })[:30000]
    try:
        drafts = json_call(MATCH_SYS, user, FindingDraftList).findings
    except ValueError:
        if cell["findings"]:
            return {**cell, "note": "scanner findings only; model analysis failed"}
        return {**cell, "status": "analysis failed"}

    urls = set(got["urls"])
    for d in drafts:
        if not 0 <= d.rule_index < len(got["rules"]):
            cell["dropped_count"] += 1
            continue
        rule = got["rules"][d.rule_index]
        if not evidence_ok(d, scan, urls, rule):
            cell["dropped_count"] += 1
            continue
        if (d.evidence.file, d.evidence.line) in {(f["evidence"]["file"], f["evidence"]["line"])
                                                   for f in cell["findings"] if f["origin"] == "scanner"}:
            continue                                         # already reported by a scanner
        key = f"{market}|{domain}|{d.title}|{d.evidence.file}|{d.evidence.line}|{d.evidence.missing_artifact}"
        cell["findings"].append(Finding(
            id=hashlib.sha1(key.encode()).hexdigest()[:10], market=market, domain=domain,
            severity=d.severity, title=d.title.strip() or d.fix_summary.strip()[:90], why=d.why, evidence=d.evidence, rule=rule,
            fix_type=d.fix_type, fix_summary=d.fix_summary).model_dump())
    return cell


def draft_policy_section(finding: dict, facts: dict) -> str:
    user = json.dumps({
        "issue": finding["title"], "why": finding["why"], "fix": finding["fix_summary"],
        "rule": finding["rule"]["requirement"],
        "facts": {k: facts.get(k) for k in ("personal_data", "third_parties", "policy_declares")},
        "vendor_enrichment": [e for e in facts.get("enrichment", []) if e.get("kind") == "vendor"],
    })
    return text_call(DRAFT_SYS, user, max_tokens=600)
