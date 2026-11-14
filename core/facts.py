"""ScanResult + key files -> FactSheet. One model call, then a code-side check."""
import json

from core.llm import json_call, numbered
from core.schema import FactExtract, FactSheet, ScanResult

FACTS_SYS = """You read source files of a software product and extract facts for a release review.
Extract facts only. Every item needs a `file` and a `line` that appear in the input (line numbers
are printed at the start of each line). Do not infer legal conclusions. If unsure, omit the item.

Fields:
- personal_data: personal data the code collects or stores. {kind, file, line, purpose}
- third_parties: external services the code sends data to. {name, data_sent, destination_country, file, line}
  Use destination_country only if the code or comments state it; otherwise "unknown".
- consent_points: places where the code asks for or records user consent. {file, line, covers}
  Leave empty if there are none.
- crypto: encryption algorithms used. {algorithm, file, line}
- ai_features: calls to AI/ML models. {feature, file, line}
- policy_declares: short statements of what the privacy policy says is collected and shared."""

MAX_INPUT_CHARS = 40_000


def render_files(key_files: dict[str, str], budget: int = MAX_INPUT_CHARS) -> str:
    parts, used = [], 0
    for path, content in key_files.items():
        block = f"=== FILE: {path} ===\n{numbered(content)}\n"
        if used + len(block) > budget:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def _keep(items: list[dict], files: set[str]) -> list[dict]:
    """Drop any item whose file is not in the repo."""
    return [i for i in items if isinstance(i, dict) and i.get("file") in files]


def build_fact_sheet(scan: ScanResult) -> FactSheet:
    user = (
        f"Scanner output:\n{json.dumps({'secrets': scan.secrets, 'vulns': scan.vulns}, indent=1)[:4000]}\n\n"
        f"Files:\n{render_files(scan.key_files)}"
    )
    ext = json_call(FACTS_SYS, user, FactExtract)
    files = set(scan.files)
    readme = " ".join(v for k, v in scan.key_files.items() if k.lower().startswith("readme")).lower()
    closed = any(w in readme for w in ("closed source", "closed-source", "proprietary", "all rights reserved"))
    return FactSheet(
        personal_data=_keep(ext.personal_data, files),
        third_parties=_keep(ext.third_parties, files),
        consent_points=_keep(ext.consent_points, files),
        crypto=_keep(ext.crypto, files),
        ai_features=_keep(ext.ai_features, files),
        policy_declares=ext.policy_declares,
        secrets=scan.secrets, vulns=scan.vulns, licences=scan.licences,
        distribution="closed-source SaaS" if closed else "unknown (no licence statement found in README)",
    )
