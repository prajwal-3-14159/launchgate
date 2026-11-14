"""Daytona: the only place repo code is cloned, installed and scanned."""
import json
import re
import shlex

from daytona import Daytona
from dotenv import load_dotenv

from core.schema import ScanResult

load_dotenv()

VENV = "/tmp/lgvenv"                       # the repo's dependencies, kept apart from the scanners
KEYWORDS = "email|location|phone|requests\\.|httpx|track|analytics|encrypt|AES|RSA|openai|consent"
ALWAYS_READ = re.compile(r"(^|/)(README[^/]*|PRIVACY[^/]*|privacy[^/]*|requirements\.txt|\.env\.example)$")
SOURCE_EXT = (".py", ".md", ".txt", ".toml", ".cfg", ".yml", ".yaml", ".json", ".example")


def _json(text: str, default):
    """Scanner stdout can carry warnings before the JSON; take the outermost JSON value."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return default
    try:
        return json.loads(text[min(starts):max(text.rfind("}"), text.rfind("]")) + 1])
    except ValueError:
        return default


def _repo_dir(sb) -> str:
    return f"{sb.get_work_dir().rstrip('/')}/repo"


def _runner(sb):
    root = _repo_dir(sb)

    def run(cmd: str, timeout: int = 300):
        # pip installs the scanners' entry points to ~/.local/bin, which is not on the sandbox PATH
        return sb.process.exec(f'export PATH="$HOME/.local/bin:$PATH"; {cmd}', cwd=root, timeout=timeout)
    return run


def _scan_secrets(run) -> list[dict]:
    out = run("detect-secrets scan --all-files 2>/dev/null").result
    results = _json(out, {}).get("results", {})
    return [
        {"file": path, "line": hit.get("line_number"), "type": hit.get("type")}
        for path, hits in results.items() for hit in hits
    ]


def _scan_vulns(run) -> list[dict]:
    # --no-deps audits the pinned lines without building them; fall back to full resolution.
    out = run("pip-audit -r requirements.txt --no-deps --disable-pip -f json 2>/dev/null").result
    data = _json(out, None)
    if data is None:
        data = _json(run("pip-audit -r requirements.txt -f json 2>/dev/null", 600).result, {})
    deps = data.get("dependencies", []) if isinstance(data, dict) else data
    seen, out = set(), []
    for d in deps:
        for v in d.get("vulns", []):
            key = (d.get("name"), v.get("id"))
            if key not in seen:                              # pip-audit can list an advisory twice
                seen.add(key)
                out.append({"package": d.get("name"), "version": d.get("version"),
                            "vuln_id": v.get("id"), "fix_versions": v.get("fix_versions", [])})
    return out


def _scan_licences(run) -> list[dict]:
    run(f"python3 -m venv {VENV}", 120)
    # one package at a time so a single failed build does not hide the others
    run(f"grep -vE '^\\s*(#|$)' requirements.txt | xargs -n1 {VENV}/bin/pip install -q 2>&1 | tail -5", 900)
    out = run(f"pip-licenses --python {VENV}/bin/python --format=json 2>/dev/null").result
    return [
        {"package": p.get("Name"), "version": p.get("Version"), "licence": p.get("License")}
        for p in _json(out, [])
        if p.get("Name", "").lower() not in ("pip", "setuptools", "wheel")
    ]


def _scan_sast(run) -> list[dict]:
    """bandit: static analysis of the Python source. Medium and high severity only."""
    out = run("bandit -r . -f json -q 2>/dev/null", 300).result
    return [
        {"file": r.get("filename", "").removeprefix("./"), "line": r.get("line_number"),
         "test_id": r.get("test_id"), "issue": r.get("issue_text"), "severity": r.get("issue_severity")}
        for r in _json(out, {}).get("results", [])
        if r.get("issue_severity") in ("MEDIUM", "HIGH")
    ]


def _key_files(run, files: list[str], secrets: list[dict]) -> dict[str, str]:
    wanted = [f for f in files if ALWAYS_READ.search(f)]
    wanted += [s["file"] for s in secrets if s.get("file") in files]
    hits = run(f"git grep -lE {shlex.quote(KEYWORDS)} -- . 2>/dev/null").result.splitlines()
    wanted += [f for f in hits if f in files and f.endswith(SOURCE_EXT)][:15]
    out: dict[str, str] = {}
    for path in dict.fromkeys(wanted):                       # de-duplicate, keep order
        out[path] = run(f"head -n 300 {shlex.quote(path)}").result
    return out


def scan_repo(repo_url: str) -> ScanResult:
    d = Daytona()                                            # reads DAYTONA_API_KEY
    sb = d.create()
    try:
        sb.git.clone(repo_url, _repo_dir(sb))
        run = _runner(sb)
        run("pip install -q detect-secrets pip-audit pip-licenses bandit 2>&1 | tail -3", 600)
        files = run("git ls-files").result.splitlines()
        secrets = _scan_secrets(run)
        vulns = _scan_vulns(run)
        licences = _scan_licences(run)
        sast = _scan_sast(run)
        return ScanResult(
            sandbox_id=sb.id, files=files, secrets=secrets, vulns=vulns, sast=sast,
            licences=licences, key_files=_key_files(run, files, secrets),
        )
    except Exception:
        sb.delete()
        raise


def verify_fix(sandbox_id: str, diff: str, finding: dict) -> tuple[bool, str]:
    """Apply the patch inside the sandbox and re-run the scanner that raised the finding."""
    d = Daytona()
    sb = d.get(sandbox_id)
    run = _runner(sb)
    sb.fs.upload_file(diff.encode(), "/tmp/lg_fix.diff")
    run("git checkout -q -- . && git clean -fdq")            # start from the scanned state every time
    applied = run("git apply --whitespace=nowarn /tmp/lg_fix.diff 2>&1")
    if applied.exit_code != 0:
        return False, f"git apply failed: {applied.result.strip()[:500]}"
    file = (finding.get("evidence") or {}).get("file")
    text = f"{finding.get('title', '')} {finding.get('why', '')}".lower()
    if any(w in text for w in ("vulnerab", "cve", "ghsa", "pysec")) or file == "requirements.txt":
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*[=<>~]", (finding.get("evidence") or {}).get("snippet") or "")
        pkg = m.group(1).lower().replace("_", "-") if m else None
        left = [v for v in _scan_vulns(run) if not pkg or str(v["package"]).lower().replace("_", "-") == pkg]
        return (not left), f"pip-audit after patch: {len(left)} known vulnerabilities remain for {pkg or 'all packages'} {left[:3]}"
    left = [s for s in _scan_secrets(run) if s["file"] == file]
    # the scanner misses a secret kept as a default argument, so also look for the literal itself
    for literal in re.findall(r"""["']([^"']{16,})["']""", (finding.get("evidence") or {}).get("snippet") or ""):
        if run(f"grep -rnF --exclude-dir=.git -- {shlex.quote(literal)} . | head -3").result.strip():
            return False, f"the secret literal is still present in the repository after the patch ({file})"
    return (not left), f"detect-secrets after patch: {len(left)} findings remain in {file} {left[:3]}"


def delete_sandbox(sandbox_id: str) -> None:
    d = Daytona()
    d.get(sandbox_id).delete()
