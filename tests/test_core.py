"""Offline tests for core/: no API keys, no network.
The model (core.llm.text_call) and Tavily (core.rules.tavily) are replaced by fakes.
Run from launchgate/:  .venv/bin/python -m pytest tests -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEMO = ROOT.parent / "demo-repo"

import core.enrich as enrich_mod   # noqa: E402
import core.llm as llm             # noqa: E402
import core.rules as rules_mod     # noqa: E402
from core.enrich import enrich, pick_subjects          # noqa: E402
from core.facts import FACTS_SYS, build_fact_sheet     # noqa: E402
from core.match import MATCH_SYS, match_cell           # noqa: E402
from core.rules import RULES_SYS, fetch_rules          # noqa: E402
from core.schema import FactSheet, FindingDraftList, QueryList, RuleDraftList, ScanResult  # noqa: E402

KEY_LINE = 'PAYMENTS_API_KEY = "lg_demo_9f8a7c6b5d4e3f2a1b0c9d8e7f6a5b4c"'
URL_A = "https://www.ppc.go.jp/en/legal/"
URL_B = "https://example.org/security-guidance"


# ---------------------------------------------------------------- fixtures

class FakeLLM:
    """Returns canned replies chosen by a marker found in the system prompt.
    A reply may be a list; then successive calls consume it (last one repeats)."""

    def __init__(self):
        self.replies: dict[str, object] = {}
        self.calls: list[tuple[str, str]] = []

    def set(self, system_prompt: str, reply):
        self.replies[system_prompt[:60]] = reply

    def __call__(self, system, user, max_tokens=2000):
        self.calls.append((system, user))
        for marker, reply in self.replies.items():
            if marker in system:
                if isinstance(reply, list):
                    reply = reply.pop(0) if len(reply) > 1 else reply[0]
                return reply if isinstance(reply, str) else json.dumps(reply)
        raise AssertionError(f"no canned reply for system prompt: {system[:80]!r}")


class FakeTavily:
    def __init__(self, results=None, error=None):
        self.results = results if results is not None else []
        self.error = error
        self.queries: list[tuple[str, dict]] = []

    def search(self, query, **kwargs):
        self.queries.append((query, kwargs))
        if self.error:
            raise self.error
        return {"results": self.results}


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "text_call", fake)
    return fake


@pytest.fixture
def fake_tavily(monkeypatch, tmp_path):
    fake = FakeTavily()
    monkeypatch.setattr(rules_mod, "tavily", lambda: fake)
    monkeypatch.setattr(rules_mod, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(enrich_mod, "CACHE", tmp_path / "cache")   # imported by value
    return fake


@pytest.fixture
def scan() -> ScanResult:
    names = ["README.md", "PRIVACY.md", "requirements.txt", "config.py",
             "analytics.py", "signup.py", "crypto_utils.py", "app.py"]
    key_files = {n: (DEMO / n).read_text() for n in names}
    assert key_files["config.py"].splitlines()[7] == KEY_LINE     # planted secret is on line 8
    return ScanResult(
        sandbox_id="sb-test", files=names,
        secrets=[{"file": "config.py", "line": 8, "type": "Secret Keyword"}],
        vulns=[{"package": "pyyaml", "version": "5.3", "vuln_id": "PYSEC-2020-96",
                "fix_versions": ["5.3.1"]}],
        licences=[{"package": "PyMuPDF", "version": "1.24.9", "licence": "GNU AFFERO GPL 3.0"},
                  {"package": "Flask", "version": "2.3.3", "licence": "BSD License"}],
        key_files=key_files,
    )


@pytest.fixture
def facts(scan) -> FactSheet:
    return FactSheet(
        personal_data=[{"kind": "email", "file": "analytics.py", "line": 12, "purpose": "analytics"}],
        third_parties=[{"name": "MetricFlow", "data_sent": "email", "destination_country": "United States",
                        "file": "analytics.py", "line": 16}],
        policy_declares=["email address collected", "no sharing with third parties"],
        secrets=scan.secrets, vulns=scan.vulns, licences=scan.licences,
    )


def pages():
    return [
        {"url": URL_A, "title": "APPI guidance", "content": "short",
         "raw_content": "Consent is required before a cross-border transfer of personal data."},
        {"url": URL_B, "title": "Security guidance", "content": "Secrets must not be stored in source code."},
    ]


def rule_reply(*urls):
    return {"rules": [{"name": f"Rule {i}", "requirement": f"Requirement {i}.", "source_url": u}
                      for i, u in enumerate(urls)]}


def draft(**over):
    base = {"severity": "blocker", "title": "Hard-coded API key", "why": "Secret in source.",
            "evidence": {"file": "config.py", "line": 8, "snippet": KEY_LINE},
            "rule_index": 0, "fix_type": "code", "fix_summary": "Read the key from an environment variable."}
    base.update(over)
    return base


def run_cell(fake_llm, fake_tavily, facts, scan, drafts, market="JP", domain="security"):
    fake_tavily.results = pages()
    fake_llm.set(RULES_SYS, rule_reply(URL_A, URL_B))
    fake_llm.set(MATCH_SYS, drafts if isinstance(drafts, str) else {"findings": drafts})
    return match_cell(market, domain, facts, scan)


# ---------------------------------------------------------------- 1. json_call

def test_json_call_parses_fenced_json(fake_llm):
    fake_llm.set("SYS", 'Sure, here it is:\n```json\n{"items": [{"subject": "a", "query": "q"}]}\n```\nDone.')
    out = llm.json_call("SYS", "u", QueryList)
    assert out.items[0].query == "q"
    assert len(fake_llm.calls) == 1


def test_json_call_strips_think_block_and_leading_brackets(fake_llm):
    fake_llm.set("SYS", '<think>maybe {"x": 1} or [draft]</think>Result [final]: {"items": []} hope that helps }')
    assert llm.json_call("SYS", "u", QueryList).items == []


def test_json_call_accepts_bare_list_for_single_field_model(fake_llm):
    fake_llm.set("SYS", '[{"subject": "a", "query": "q"}]')
    assert llm.json_call("SYS", "u", QueryList).items[0].subject == "a"


def test_json_call_invalid_twice_raises(fake_llm):
    fake_llm.set("SYS", "I cannot produce JSON, sorry.")
    with pytest.raises(ValueError, match="invalid JSON twice"):
        llm.json_call("SYS", "u", QueryList)
    assert len(fake_llm.calls) == 2


def test_json_call_schema_violation_twice_raises(fake_llm):
    fake_llm.set("SYS", '{"items": [{"subject": "a"}]}')        # query missing
    with pytest.raises(ValueError):
        llm.json_call("SYS", "u", QueryList)


def test_json_call_retry_succeeds_and_passes_error_back(fake_llm):
    fake_llm.set("SYS", ["not json at all", '{"items": []}'])
    assert llm.json_call("SYS", "u", QueryList).items == []
    assert len(fake_llm.calls) == 2
    assert "previous reply was rejected" in fake_llm.calls[1][1]


def test_numbered_matches_file_line_numbers():
    out = llm.numbered("a\nb\nc")
    assert out.splitlines()[2].split() == ["3", "c"]


# ---------------------------------------------------------------- 2. build_fact_sheet

def test_fact_sheet_drops_unknown_files_and_copies_scanner_output(fake_llm, scan):
    fake_llm.set(FACTS_SYS, {
        "personal_data": [
            {"kind": "email", "file": "analytics.py", "line": 12, "purpose": "analytics"},
            {"kind": "location", "file": "signup.py", "line": "15", "purpose": "home location"},
            {"kind": "phone", "file": "models/user.py", "line": 3, "purpose": "invented"},
            "a stray string instead of an object",
        ],
        "third_parties": [
            {"name": "MetricFlow", "data_sent": "email", "destination_country": "United States",
             "file": "analytics.py", "line": 16},
            {"name": "Ghost Inc", "data_sent": "x", "destination_country": "unknown", "file": "ghost.py", "line": 1},
        ],
        "consent_points": None,
        "crypto": [{"algorithm": "AES-256-GCM", "file": "crypto_utils.py", "line": 9}],
        "ai_features": [],
        "policy_declares": ["email address", "no third-party sharing"],
    })
    sheet = build_fact_sheet(scan)
    assert [p["kind"] for p in sheet.personal_data] == ["email", "location"]
    assert [t["name"] for t in sheet.third_parties] == ["MetricFlow"]
    assert sheet.consent_points == []                           # planted issue 4: no consent step
    assert sheet.crypto[0]["algorithm"] == "AES-256-GCM"
    assert sheet.secrets == scan.secrets and sheet.vulns == scan.vulns and sheet.licences == scan.licences
    assert sheet.distribution == "closed-source SaaS"
    # the model was shown numbered file text including the planted line
    assert f"   8  {KEY_LINE}" in fake_llm.calls[0][1]


def test_fact_sheet_distribution_unknown_without_notice(fake_llm, scan):
    fake_llm.set(FACTS_SYS, {})
    scan.key_files["README.md"] = "# Some app\nMIT licensed."
    assert build_fact_sheet(scan).distribution.startswith("unknown")


# ---------------------------------------------------------------- 3. fetch_rules

def test_fetch_rules_rejects_unfetched_url(fake_llm, fake_tavily, facts):
    fake_tavily.results = pages()
    fake_llm.set(RULES_SYS, rule_reply(URL_A, "https://made-up.example/law", URL_B))
    got = fetch_rules("JP", "privacy", facts)
    assert [r.source_url for r in got["rules"]] == [URL_A, URL_B]
    assert got["rules"][0].source_title == "APPI guidance"
    assert got["rules"][0].market == "JP" and got["rules"][0].domain == "privacy"
    assert set(got["urls"]) == {URL_A, URL_B}
    assert got["cached"] is False and got["error"] is None and got["fetched_at"]


def test_fetch_rules_caps_at_four_and_tolerates_trailing_slash(fake_llm, fake_tavily, facts):
    fake_tavily.results = pages()
    fake_llm.set(RULES_SYS, rule_reply(URL_A.rstrip("/"), URL_A, URL_B, URL_B, URL_A, URL_A))
    got = fetch_rules("JP", "privacy", facts)
    assert len(got["rules"]) == 4
    assert got["rules"][0].source_url == URL_A                  # stored as fetched, not as the model wrote it


def test_fetch_rules_second_call_uses_cache(fake_llm, fake_tavily, facts):
    fake_tavily.results = pages()
    fake_llm.set(RULES_SYS, rule_reply(URL_A))
    first = fetch_rules("JP", "privacy", facts)
    assert len(fake_tavily.queries) == 1
    second = fetch_rules("JP", "privacy", facts)
    assert len(fake_tavily.queries) == 1                        # no new search
    assert second["cached"] is True
    assert second["fetched_at"] == first["fetched_at"]
    assert second["rules"][0].fetched_at == first["fetched_at"]
    assert (rules_mod.CACHE / "rules" / "JP_privacy.json").exists()


def test_fetch_rules_uses_raw_content_and_official_domains(fake_llm, fake_tavily, facts):
    fake_tavily.results = pages()
    fake_llm.set(RULES_SYS, rule_reply(URL_A))
    fetch_rules("JP", "privacy", facts)
    query, kwargs = fake_tavily.queries[0]
    assert "Japan" in query and kwargs["include_domains"] == ["ppc.go.jp", "meti.go.jp"]
    assert "cross-border transfer of personal data" in fake_llm.calls[0][1]


def test_fetch_rules_empty_results(fake_llm, fake_tavily, facts):
    fake_tavily.results = []
    got = fetch_rules("EU", "trade", facts)
    assert got["rules"] == [] and got["error"] == "no rules retrieved"
    assert len(fake_tavily.queries) == 2                        # retried without include_domains
    assert "include_domains" not in fake_tavily.queries[1][1]
    assert fake_llm.calls == []                                 # no model call without pages
    assert not (rules_mod.CACHE / "rules" / "EU_trade.json").exists()


def test_fetch_rules_all_rules_rejected_reports_none(fake_llm, fake_tavily, facts):
    fake_tavily.results = pages()
    fake_llm.set(RULES_SYS, rule_reply("https://made-up.example/1"))
    got = fetch_rules("JP", "privacy", facts)
    assert got["rules"] == [] and got["error"] == "no rules retrieved"


# ---------------------------------------------------------------- 4. enrich

def test_pick_subjects_covers_all_kinds(facts):
    subs = pick_subjects(facts)
    assert {s["kind"] for s in subs} == {"vendor", "vuln", "licence"}
    assert [s["subject"] for s in subs if s["kind"] == "licence"] == ["PyMuPDF (GNU AFFERO GPL 3.0)"]   # Flask/BSD skipped
    assert any("PYSEC-2020-96" in s["subject"] for s in subs)


def test_pick_subjects_caps_at_eight_and_keeps_every_kind(facts):
    facts.vulns = [{"package": "p", "version": "1", "vuln_id": f"CVE-{i}", "fix_versions": []} for i in range(20)]
    subs = pick_subjects(facts)
    assert len(subs) == 8
    assert {s["kind"] for s in subs} == {"vendor", "vuln", "licence"}
    assert {s["kind"] for s in subs[:3]} == {"vendor", "vuln", "licence"}   # one of each first


def test_pick_subjects_empty():
    assert pick_subjects(FactSheet()) == []
    assert enrich(FactSheet()).enrichment == []


def test_enrich_keeps_cited_and_drops_uncited(fake_llm, fake_tavily, facts):
    fake_tavily.results = [{"url": "https://metricflow.example/dpa", "title": "DPA", "content": "Data stored in the US."}]
    subs = pick_subjects(facts)
    fake_llm.set(enrich_mod.QUERY_SYS, {"items": [{"subject": s["subject"], "query": f"q {i}"} for i, s in enumerate(subs)]})

    def summary(system, user, max_tokens=2000):
        subject = json.loads(user)["subject"]
        url = "https://metricflow.example/dpa" if subject["kind"] == "vendor" else "https://invented.example/x"
        return json.dumps({"subject": subject["subject"], "kind": subject["kind"],
                           "summary": "Stores data in the US; DPA published.", "source_url": url})

    inner = fake_llm.__call__
    monkeypatched = lambda s, u, max_tokens=2000: summary(s, u) if enrich_mod.SUMMARY_SYS[:40] in s else inner(s, u)
    llm.text_call = monkeypatched                                # fixture's monkeypatch restores the original
    out = enrich(facts)
    assert [e["kind"] for e in out.enrichment] == ["vendor"]     # vuln + licence cited an unfetched URL
    e = out.enrichment[0]
    assert e["subject"] == "MetricFlow" and e["source_url"] == "https://metricflow.example/dpa"
    assert e["fetched_at"] and e["cached"] is False and e["query"] == "q 0"
    assert len(fake_tavily.queries) == len(subs)
    assert all(kw.get("search_depth") == "basic" for _, kw in fake_tavily.queries)


def test_enrich_falls_back_to_template_queries_when_model_fails(fake_llm, fake_tavily, facts):
    fake_tavily.results = []
    fake_llm.set(enrich_mod.QUERY_SYS, "no json")
    out = enrich(facts)
    assert out.enrichment == []
    assert len(fake_tavily.queries) == 3                         # still searched, with template queries
    assert any("MetricFlow" in q for q, _ in fake_tavily.queries)


# ---------------------------------------------------------------- 5. match_cell / evidence check

def test_match_keeps_correct_snippet_at_correct_line(fake_llm, fake_tavily, facts, scan):
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft()])
    assert cell["status"] == "ok" and cell["dropped_count"] == 0
    (f,) = cell["findings"]
    assert f["evidence"]["file"] == "config.py" and f["evidence"]["line"] == 8
    assert f["rule"]["source_url"] == URL_A and f["rule"]["fetched_at"]
    assert f["market"] == "JP" and f["domain"] == "security" and len(f["id"]) == 10
    assert len(cell["rules"]) == 2 and cell["cached"] is False


@pytest.mark.parametrize("line", [5, 6, 7, 9, 10, 11])
def test_match_keeps_snippet_within_three_lines(fake_llm, fake_tavily, facts, scan, line):
    ev = {"file": "config.py", "line": line, "snippet": KEY_LINE}
    assert len(run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])["findings"]) == 1


@pytest.mark.parametrize("line", [1, 4, 12, 400, 0, -3])
def test_match_drops_snippet_more_than_three_lines_off(fake_llm, fake_tavily, facts, scan, line):
    ev = {"file": "config.py", "line": line, "snippet": KEY_LINE}
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])
    assert cell["findings"] == [] and cell["dropped_count"] == 1


def test_match_drops_invented_snippet(fake_llm, fake_tavily, facts, scan):
    ev = {"file": "config.py", "line": 8, "snippet": 'AWS_SECRET = "AKIA000000000000"'}
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])
    assert cell["findings"] == [] and cell["dropped_count"] == 1


def test_match_drops_trivial_snippet(fake_llm, fake_tavily, facts, scan):
    ev = {"file": "config.py", "line": 8, "snippet": "="}
    assert run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])["dropped_count"] == 1


def test_match_drops_file_not_in_repo(fake_llm, fake_tavily, facts, scan):
    ev = {"file": "settings/secrets.py", "line": 8, "snippet": KEY_LINE}
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])
    assert cell["findings"] == [] and cell["dropped_count"] == 1


def test_match_drops_missing_artifact_pointing_at_unknown_file(fake_llm, fake_tavily, facts, scan):
    ev = {"file": "docs/DPA.md", "missing_artifact": "no transfer clause"}
    assert run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])["dropped_count"] == 1


def test_match_drops_file_whose_text_was_not_fetched(fake_llm, fake_tavily, facts, scan):
    del scan.key_files["config.py"]                             # in repo, but nothing to verify against
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft()])
    assert cell["findings"] == [] and cell["dropped_count"] == 1


def test_match_keeps_missing_artifact_only(fake_llm, fake_tavily, facts, scan):
    d = draft(title="Transfer not disclosed", fix_type="document",
              evidence={"missing_artifact": "no cross-border transfer disclosure in PRIVACY.md"})
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [d], domain="privacy")
    (f,) = cell["findings"]
    assert f["evidence"]["missing_artifact"].startswith("no cross-border") and f["evidence"]["file"] is None


def test_match_keeps_missing_artifact_naming_a_repo_file(fake_llm, fake_tavily, facts, scan):
    d = draft(fix_type="document", evidence={"file": "PRIVACY.md", "line": None, "snippet": "",
                                             "missing_artifact": "no mention of MetricFlow in PRIVACY.md"})
    assert len(run_cell(fake_llm, fake_tavily, facts, scan, [d], domain="privacy")["findings"]) == 1


def test_match_missing_artifact_does_not_rescue_a_false_snippet(fake_llm, fake_tavily, facts, scan):
    ev = {"file": "config.py", "line": 8, "snippet": "totally invented line", "missing_artifact": "something"}
    assert run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])["dropped_count"] == 1


def test_match_drops_no_evidence_at_all(fake_llm, fake_tavily, facts, scan):
    for ev in ({}, None, {"missing_artifact": "   "}):
        cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)])
        assert cell["status"] == "ok" and cell["findings"] == [] and cell["dropped_count"] == 1


def test_match_drops_empty_fix_summary(fake_llm, fake_tavily, facts, scan):
    for fs in ("", "   ", None):
        cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft(fix_summary=fs)])
        assert cell["findings"] == [] and cell["dropped_count"] == 1


@pytest.mark.parametrize("idx", [2, 99, -1])
def test_match_drops_rule_index_out_of_range(fake_llm, fake_tavily, facts, scan, idx):
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft(rule_index=idx)])
    assert cell["findings"] == [] and cell["dropped_count"] == 1


def test_match_dropped_count_over_mixed_batch(fake_llm, fake_tavily, facts, scan):
    drafts = [
        draft(),                                                                    # keep
        draft(rule_index=1, title="Second rule"),                                   # keep
        draft(evidence={"missing_artifact": "no SBOM"}, title="No SBOM"),           # keep
        draft(evidence={"file": "config.py", "line": 1, "snippet": KEY_LINE}),      # wrong line
        draft(evidence={"file": "config.py", "line": 8, "snippet": "nope nope"}),   # invented
        draft(evidence={"file": "x.py", "line": 8, "snippet": KEY_LINE}),           # no such file
        draft(fix_summary=""),                                                      # no fix
        draft(rule_index=7),                                                        # no such rule
    ]
    cell = run_cell(fake_llm, fake_tavily, facts, scan, drafts)
    assert len(cell["findings"]) == 3 and cell["dropped_count"] == 5
    assert len({f["id"] for f in cell["findings"]}) == 3


def test_match_tolerates_sloppy_model_output(fake_llm, fake_tavily, facts, scan):
    sloppy = draft(severity="Blocker", fix_type="Code", rule_index="1",
                   evidence={"file": "config.py", "line": "L8", "snippet": f"   8  {KEY_LINE}  ",
                             "missing_artifact": "null"})
    reply = "```json\n" + json.dumps([sloppy]) + "\n```"         # bare list, fenced
    cell = run_cell(fake_llm, fake_tavily, facts, scan, reply)
    (f,) = cell["findings"]
    assert f["severity"] == "blocker" and f["fix_type"] == "code" and f["evidence"]["line"] == 8
    assert f["rule"]["source_url"] == URL_B


def test_match_whitespace_differences_in_snippet_are_ok(fake_llm, fake_tavily, facts, scan):
    ev = {"file": "signup.py", "line": 14, "snippet": '"home_location":   {"lat": latitude, "lon": longitude},'}
    line = scan.key_files["signup.py"].splitlines()[13]
    assert "home_location" in line
    assert len(run_cell(fake_llm, fake_tavily, facts, scan, [draft(evidence=ev)], domain="privacy")["findings"]) == 1


def test_match_llm_invalid_twice_is_analysis_failed(fake_llm, fake_tavily, facts, scan):
    cell = run_cell(fake_llm, fake_tavily, facts, scan, "I am not sure how to answer.")
    assert cell["status"] == "analysis failed" and cell["findings"] == [] and cell["dropped_count"] == 0
    assert len(cell["rules"]) == 2                               # rules were fetched; only matching failed


def test_match_search_exception_is_reported(fake_llm, fake_tavily, facts, scan):
    fake_tavily.error = RuntimeError("401 unauthorized")
    cell = match_cell("JP", "privacy", facts, scan)
    assert cell["status"].startswith("rule search failed") and "401" in cell["status"]
    assert cell["findings"] == [] and cell["rules"] == []


def test_match_rules_llm_failure_is_reported_not_raised(fake_llm, fake_tavily, facts, scan):
    fake_tavily.results = pages()
    fake_llm.set(RULES_SYS, "garbage")
    cell = match_cell("JP", "privacy", facts, scan)
    assert cell["status"] == "analysis failed" and cell["findings"] == []


def test_match_no_rules_retrieved(fake_llm, fake_tavily, facts, scan):
    fake_tavily.results = []
    cell = match_cell("IN", "trade", facts, scan)
    assert cell["status"] == "no rules retrieved" and cell["findings"] == []


def test_match_cell_result_is_json_serialisable(fake_llm, fake_tavily, facts, scan):
    cell = run_cell(fake_llm, fake_tavily, facts, scan, [draft()])
    assert json.loads(json.dumps(cell))["findings"][0]["rule"]["source_url"] == URL_A


def test_match_prompt_carries_file_lines_and_enrichment(fake_llm, fake_tavily, facts, scan):
    facts.enrichment = [{"subject": "MetricFlow", "kind": "vendor", "summary": "US storage",
                         "source_url": "https://metricflow.example/dpa", "fetched_at": "2026-11-14T00:00:00+00:00"}]
    run_cell(fake_llm, fake_tavily, facts, scan, [], domain="privacy")
    payload = json.loads(fake_llm.calls[-1][1])
    assert payload["facts"]["enrichment"][0]["source_url"] == "https://metricflow.example/dpa"
    assert "analytics.py" in payload["file_lines"] and "secrets" not in payload["facts"]


def test_schema_lists_parse():
    assert FindingDraftList.model_validate({"findings": []}).findings == []
    assert RuleDraftList.model_validate({}).rules == []


# ---- deterministic scanner findings -------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_scanner_findings(request, monkeypatch):
    """The evidence-check tests above are about model findings; scanner findings are tested below."""
    if "scanner" not in request.node.name:
        monkeypatch.setattr("core.match.scanner_findings", lambda *a, **k: [])


def test_scanner_findings_security_are_deterministic(facts, scan):
    from core.match import scanner_findings
    out = scanner_findings("JP", "security", facts, scan, [])
    titles = [f["title"] for f in out]
    assert any("Hard-coded secret in config.py" in t for t in titles)
    assert any("pyyaml" in t.lower() and "PYSEC" in t for t in titles)
    assert all(f["origin"] == "scanner" and f["evidence"]["snippet"] for f in out)
    secret = next(f for f in out if "secret" in f["title"])
    assert secret["evidence"]["line"] == 8 and "PAYMENTS_API_KEY" in secret["evidence"]["snippet"]
    assert out == scanner_findings("JP", "security", facts, scan, [])


def test_scanner_findings_licence_only_when_closed_source(facts, scan):
    from core.match import scanner_findings
    out = scanner_findings("EU", "licences", facts, scan, [])
    assert len(out) == 1 and "PyMuPDF" in out[0]["title"] and out[0]["severity"] == "warning"
    assert out[0]["evidence"]["file"] == "requirements.txt"
    opened = facts.model_copy(update={"distribution": "open-source"})
    assert scanner_findings("EU", "licences", opened, scan, []) == []


def test_scanner_findings_none_for_privacy_and_trade(facts, scan):
    from core.match import scanner_findings
    assert scanner_findings("JP", "privacy", facts, scan, []) == []
    assert scanner_findings("JP", "trade", facts, scan, []) == []
