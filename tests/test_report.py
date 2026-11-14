"""Offline tests for core/report.py. The model is replaced by a fake text_call."""
import html
import json
import re
from pathlib import Path

import pytest

from core import llm, report

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "tests" / "fixtures" / "live_result.json"
SAMPLE = ROOT / "samples" / "sample_result.json"


def load(path):
    return json.loads(path.read_text())


def findings(result):
    return [f for c in result["cells"] for f in c["findings"]]


class FakeLLM:
    """Routes on the writer's format system prompt."""

    def __init__(self):
        self.replies = {}
        self.calls = 0

    def set(self, system, reply):
        self.replies[system] = reply

    def __call__(self, system, user, max_tokens=2000):
        self.calls += 1
        for key, reply in self.replies.items():
            if system.startswith(key):
                if isinstance(reply, Exception):
                    raise reply
                return reply if isinstance(reply, str) else json.dumps(reply)
        raise RuntimeError("no canned reply")


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(llm, "text_call", f)
    return f


@pytest.fixture(params=[LIVE, SAMPLE], ids=["live", "sample"])
def result(request):
    return load(request.param)


def good_replies(fake, result):
    ids = [f["id"] for f in findings(result)]
    fake.set(report.EXEC_SUMMARY_SYS, {"summary": "MODEL-SUMMARY pre-counsel triage text."})
    fake.set(report.MARKET_BRIEF_SYS, {"briefs": [{"market": m, "brief": f"MODEL-BRIEF-{m}"}
                                                 for m in result["markets"]]})
    fake.set(report.REMEDIATION_SYS, {"steps": [
        {"step": "MODEL-STEP rotate the key", "finding_ids": ids[:2], "effort": "S", "owner": "engineering"}]})


# ---------- deterministic rendering ----------
def test_renders_without_model(result, fake):
    out = report.build_report(result, use_llm=False)
    assert fake.calls == 0
    assert out.startswith("<!doctype html>") and out.rstrip().endswith("</html>")
    assert report.NO_MODEL_NOTE in out
    assert "Pre-counsel triage. Not legal advice." in out
    assert "<script" not in out.lower() and 'src="http' not in out and "<link" not in out   # self-contained


def test_contains_every_finding_title_and_source(result, fake):
    out = report.build_report(result, use_llm=False)
    for f in findings(result):
        assert html.escape(report._clean(f["title"]), quote=True) in out
        assert html.escape(f["rule"]["source_url"], quote=True) in out
        assert f["id"] in out


def test_header_and_verdict(result, fake):
    out = report.build_report(result, use_llm=False)
    b = sum(1 for f in findings(result) if f["severity"] == "blocker")
    w = sum(1 for f in findings(result) if f["severity"] == "warning")
    assert f"{b} blocker" in out and f"{w} warning" in out
    assert html.escape(result["scan"]["sandbox_id"]) in out
    assert html.escape(result["llm_host"]) in out
    assert html.escape(result["repo_url"]) in out


def test_matrix_shows_non_ok_status(fake):
    r = load(SAMPLE)
    bad = [c for c in r["cells"] if c["status"] != "ok"]
    assert bad, "sample should contain a non-ok cell"
    out = report.build_report(r, use_llm=False)
    assert f'<td class="failed">{html.escape(bad[0]["status"])}</td>' in out


def test_word_compliant_never_appears(result, fake):
    good_replies(fake, result)
    fake.set(report.EXEC_SUMMARY_SYS, {"summary": "The product is compliant and Non-Compliant at once."})
    for use_llm in (False, True):
        out = report.build_report(result, use_llm=use_llm)
        prose = re.sub(r"https?://[^\s\"<]+", "", out)      # source URLs are shown verbatim
        assert not re.search(r"compliant", prose, flags=re.I)


def test_no_findings_verdict(fake):
    r = load(SAMPLE)
    for c in r["cells"]:
        c["findings"] = []
    out = report.build_report(r, use_llm=True)
    assert "no blockers found in these checks" in out
    assert fake.calls == 0                      # nothing to write about, no model call


def test_dropped_count_and_context(fake):
    r = load(SAMPLE)
    r["dropped_total"] = 7
    r["context"] = {"app_description": "Trip planner <b>beta</b>", "deploy_regions": ["ap-northeast-1", "eu-west-1"]}
    out = report.build_report(r, use_llm=False)
    assert "<strong>7</strong> candidate" in out
    assert "Trip planner &lt;b&gt;beta&lt;/b&gt;" in out and "ap-northeast-1, eu-west-1" in out


def test_sast_and_origin_rendered(fake):
    r = load(SAMPLE)
    r["scan"]["sast"] = [{"file": "app.py", "line": 22, "rule": "B506", "severity": "MEDIUM",
                          "message": "Use of unsafe yaml load"}]
    findings(r)[0]["origin"] = "scanner"
    out = report.build_report(r, use_llm=False)
    assert "B506" in out and "Use of unsafe yaml load" in out
    assert "origin: scanner" in out and "origin: model" in out


def test_javascript_url_is_not_linked(fake):
    r = load(SAMPLE)
    findings(r)[0]["rule"]["source_url"] = "javascript:alert(1)"
    out = report.build_report(r, use_llm=False)
    assert 'href="javascript:' not in out


def test_snippet_is_escaped(fake):
    r = load(SAMPLE)
    findings(r)[0]["evidence"]["snippet"] = '<img src=x onerror="alert(1)">'
    out = report.build_report(r, use_llm=False)
    assert "<img src=x" not in out and "&lt;img src=x" in out


# ---------- writer agents ----------
def test_model_text_is_used(result, fake):
    good_replies(fake, result)
    out = report.build_report(result)
    assert "MODEL-SUMMARY" in out and "MODEL-STEP rotate the key" in out
    for m in result["markets"]:
        assert f"MODEL-BRIEF-{m}" in out
    assert report.NO_MODEL_NOTE not in out
    assert fake.calls == 3


def test_model_output_is_escaped(result, fake):
    ids = [f["id"] for f in findings(result)]
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    fake.set(report.EXEC_SUMMARY_SYS, {"summary": evil})
    fake.set(report.MARKET_BRIEF_SYS, {"briefs": [{"market": m, "brief": evil} for m in result["markets"]]})
    fake.set(report.REMEDIATION_SYS, {"steps": [{"step": evil, "finding_ids": ids[:1], "effort": "S",
                                                 "owner": "legal"}]})
    out = report.build_report(result)
    assert "<script" not in out.lower() and "<img" not in out.lower()
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in out


def test_remediation_steps_with_unknown_ids_are_dropped(result, fake):
    good_replies(fake, result)
    ids = [f["id"] for f in findings(result)]
    fake.set(report.REMEDIATION_SYS, {"steps": [
        {"step": "KEEP-ME", "finding_ids": [ids[0]], "effort": "s", "owner": "Engineering"},
        {"step": "DROP-UNKNOWN", "finding_ids": ["deadbeef00"], "effort": "M", "owner": "legal"},
        {"step": "DROP-MIXED", "finding_ids": [ids[0], "nope"], "effort": "L", "owner": "product"},
        {"step": "DROP-EMPTY", "finding_ids": [], "effort": "L", "owner": "product"}]})
    out = report.build_report(result)
    assert "KEEP-ME" in out
    assert "DROP-UNKNOWN" not in out and "DROP-MIXED" not in out and "DROP-EMPTY" not in out
    assert "deadbeef00" not in out


def test_all_steps_invalid_falls_back_to_deterministic_plan(result, fake):
    good_replies(fake, result)
    fake.set(report.REMEDIATION_SYS, {"steps": [{"step": "BAD", "finding_ids": ["zzz"]}]})
    out = report.build_report(result)
    assert "BAD" not in out and report.NO_MODEL_NOTE in out
    assert "MODEL-SUMMARY" in out               # other sections still model-written


def test_llm_exception_falls_back(result, fake):
    fake.set(report.EXEC_SUMMARY_SYS, RuntimeError("endpoint down"))
    fake.set(report.MARKET_BRIEF_SYS, "not json at all")
    fake.set(report.REMEDIATION_SYS, ConnectionError("timeout"))
    out = report.build_report(result)
    assert out.count(report.NO_MODEL_NOTE) >= 3
    assert "This pre-counsel triage checked the repository" in out
    for f in findings(result):
        assert html.escape(report._clean(f["title"]), quote=True) in out


def test_briefs_for_unknown_markets_are_ignored(fake):
    r = load(SAMPLE)
    good_replies(fake, r)
    fake.set(report.MARKET_BRIEF_SYS, {"briefs": [{"market": "ZZ", "brief": "GHOST-MARKET"},
                                                 {"market": r["markets"][0].lower(), "brief": "REAL-BRIEF"}]})
    out = report.build_report(r)
    assert "GHOST-MARKET" not in out and "REAL-BRIEF" in out


def test_empty_result_does_not_crash(fake):
    out = report.build_report({"repo_url": "https://example.com/x", "markets": ["JP"], "scan": {},
                               "facts": {}, "cells": [], "error": "ValueError: boom <b>"}, use_llm=True)
    assert "Run error: ValueError: boom &lt;b&gt;" in out and "not run" in out
