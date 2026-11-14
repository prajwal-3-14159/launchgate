"""Data contracts shared by every stage. Written first; everything else imports from here."""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Market = Literal["JP", "EU", "US", "IN"]
Domain = Literal["security", "privacy", "licences", "trade"]

MARKET_NAMES = {"JP": "Japan", "EU": "European Union", "US": "United States", "IN": "India"}
DOMAINS: list[str] = ["security", "privacy", "licences", "trade"]


class ScanResult(BaseModel):
    sandbox_id: str
    files: list[str]                 # repo-relative paths
    secrets: list[dict]              # {file, line, type}
    vulns: list[dict]                # {package, version, vuln_id, fix_versions}
    licences: list[dict]             # {package, version, licence}
    sast: list[dict] = []            # bandit: {file, line, test_id, issue, severity}
    key_files: dict[str, str]        # path -> raw content (numbered only when shown to the LLM)


class FactExtract(BaseModel):
    """The part of the fact sheet the LLM writes."""
    personal_data: list[dict] = []   # {kind, file, line, purpose}
    third_parties: list[dict] = []   # {name, data_sent, destination_country, file, line}
    consent_points: list[dict] = []  # {file, line, covers}
    crypto: list[dict] = []          # {algorithm, file, line}
    ai_features: list[dict] = []     # {feature, file, line}
    policy_declares: list[str] = []  # what the privacy policy says is collected/shared

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_empty(cls, v):
        return [] if v is None else v

    @field_validator("personal_data", "third_parties", "consent_points", "crypto", "ai_features",
                     mode="before")
    @classmethod
    def _dicts_only(cls, v):
        # one malformed item should not reject the whole sheet
        return [i for i in v if isinstance(i, dict)] if isinstance(v, list) else v


class FactSheet(FactExtract):
    secrets: list[dict] = []         # copied from ScanResult
    vulns: list[dict] = []
    licences: list[dict] = []
    distribution: str = "closed-source SaaS"
    product_context: dict = {}       # answers from the intake form
    enrichment: list[dict] = []      # {subject, kind, summary, source_url, fetched_at}


class Query(BaseModel):
    subject: str
    query: str


class QueryList(BaseModel):
    items: list[Query]


class Enrichment(BaseModel):
    subject: str
    kind: Literal["vendor", "vuln", "licence"]
    summary: str
    source_url: str


class Rule(BaseModel):
    market: Market
    domain: Domain
    name: str
    requirement: str                 # one-sentence summary written by the LLM from the page text
    source_url: str
    source_title: str
    fetched_at: str                  # ISO 8601


class RuleDraft(BaseModel):
    name: str
    requirement: str
    source_url: str


class RuleDraftList(BaseModel):
    rules: list[RuleDraft] = []


class Evidence(BaseModel):
    file: Optional[str] = None
    line: Optional[int] = None
    snippet: Optional[str] = None
    missing_artifact: Optional[str] = None   # e.g. "no cross-border transfer clause in PRIVACY.md"

    @field_validator("line", mode="before")
    @classmethod
    def _line_to_int(cls, v):
        # small models write "8", "L8", "8-9" or 8.0
        if isinstance(v, bool):
            return None
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            m = re.search(r"\d+", v)
            return int(m.group()) if m else None
        return v

    @field_validator("file", "snippet", "missing_artifact", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        if v is None:
            return None
        v = str(v)
        return v if v.strip() and v.strip().lower() not in ("null", "none", "n/a") else None


class FindingDraft(BaseModel):
    """What the LLM returns; the rule is referenced by index so it cannot invent a source."""
    severity: Literal["blocker", "warning"]
    title: str = ""
    why: str = ""
    evidence: Evidence = Field(default_factory=Evidence)
    rule_index: int
    fix_type: Literal["code", "document"]
    fix_summary: str = ""

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_null(cls, v):
        return {} if v is None else v

    @field_validator("severity", "fix_type", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("fix_summary", "why", "title", mode="before")
    @classmethod
    def _null_text(cls, v):
        return "" if v is None else v


class FindingDraftList(BaseModel):
    findings: list[FindingDraft] = []


class Finding(BaseModel):
    id: str
    market: Market
    domain: Domain
    severity: Literal["blocker", "warning"]
    title: str
    why: str
    evidence: Evidence
    rule: Rule
    fix_type: Literal["code", "document"]
    fix_summary: str
    origin: Literal["scanner", "model"] = "model"   # scanner = deterministic open-source tool result


class FixResult(BaseModel):
    """Structured answer requested from CodeAF."""
    summary: str = ""
    files_changed: list[str] = []


class Fix(BaseModel):
    finding_id: str
    diff: str
    verified: bool
    verification_log: str
