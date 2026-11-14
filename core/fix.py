"""CodeAF writes the patch on a local clone, running on the Nosana model; Daytona verifies it.
Nothing from the repo is executed locally: CodeAF only edits files."""
import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from core import llm
from core.schema import Fix

load_dotenv()

WORKDIR = Path(__file__).resolve().parent.parent / "workdir" / "repo"

FIX_PROMPT = (
    "Fix exactly this issue: {title}. Evidence: {file}:{line}. Suggested fix: {fix_summary} "
    "Make the smallest change. Edit existing files only; do not create new files or write a summary file. "
    "Do not touch unrelated files. Do not run the application. "
    "For a hard-coded secret, read it from an environment variable with no fallback value, delete the old "
    "literal from the code entirely, and add the variable name (empty) to .env.example. "
    "For a vulnerable pinned dependency, change only that pin in requirements.txt to a fixed version."
)


def fix_prompt(finding: dict, feedback: str | None = None) -> str:
    ev = finding.get("evidence") or {}
    prompt = FIX_PROMPT.format(title=finding["title"], file=ev.get("file"), line=ev.get("line"),
                               fix_summary=finding.get("fix_summary", ""))
    if feedback:                                             # the verifier's verdict on the last attempt
        prompt += (" A previous attempt at this fix was rejected by verification in the sandbox: "
                   f"{feedback[:600]} Do not repeat that mistake. Do not edit the tests.")
    return prompt


def codeaf_env() -> dict[str, str]:
    """Point CodeAF at the same OpenAI-compatible endpoint as every other model call."""
    return {
        "CODEAF_BASE_URL": os.environ["LLM_BASE_URL"],          # must end in /v1
        "OPENAI_API_KEY": os.environ.get("LLM_API_KEY") or "nosana",   # CodeAF refuses an empty key
        "CODEAF_MODEL": llm.model(),
        # An OpenRouter key outranks OPENAI_API_KEY and would be sent to our endpoint; blank it.
        "OPENROUTER_API_KEY": "",
        # Default reserve (65536) is larger than a small model's whole context window.
        "CODEAF_COMPLETION_RESERVE": os.environ.get("CODEAF_COMPLETION_RESERVE", "4096"),
    }


def prepare_workdir(repo_url: str) -> Path:
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    WORKDIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", "-q", repo_url, str(WORKDIR)],
                   check=True, capture_output=True, text=True, timeout=120)
    return WORKDIR


def collect_diff(cwd: Path = WORKDIR) -> str:
    """Changes to files already in the repo only. CodeAF's own scratch files (.codeaf/, .obs/,
    summary notes) are untracked and are left out of the patch."""
    return subprocess.run(["git", "diff", "HEAD"], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def codeaf_binary() -> str | None:
    try:
        from agentfield.harness._codeaf_install import find_codeaf_binary, install_codeaf
        return find_codeaf_binary() or install_codeaf()
    except Exception:
        return shutil.which("codeaf")


def run_codeaf_cli(prompt: str, cwd: Path = WORKDIR) -> tuple[bool, str]:
    """Fallback path from docs/HEADLESS.md: one agent, no planning."""
    binary = codeaf_binary()
    if not binary:
        return False, "codeaf binary not found"
    cmd = [binary, "exec", prompt, "--dir", str(cwd), "--model", llm.model(),
           "--max-turns", "15", "--timeout", "5m", "--json"]
    child_env = {**os.environ, **codeaf_env()}
    child_env.pop("OPENROUTER_API_KEY", None)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=330,
                           env=child_env)
    except subprocess.TimeoutExpired:
        return False, "codeaf exec timed out"
    # exit codes: 0 done, 1 error, 2 incomplete, 3 budget/turn cap (partial work may exist)
    return p.returncode == 0, (p.stdout[-1500:] + p.stderr[-500:])


def finish(finding: dict, sandbox_id: str, via: str, error: str | None = None) -> dict:
    """Read the diff CodeAF left behind and verify it in the sandbox. Never fabricates a diff."""
    from core.sandbox import verify_fix
    diff = collect_diff()
    if not diff.strip():
        return {**Fix(finding_id=finding["id"], diff="", verified=False,
                      verification_log="no patch produced").model_dump(),
                "error": error or "CodeAF made no changes", "via": via}
    try:
        verified, log = verify_fix(sandbox_id, diff, finding)
    except Exception as exc:
        verified, log = False, f"sandbox verification failed to run: {type(exc).__name__}: {exc}"
    return {**Fix(finding_id=finding["id"], diff=diff, verified=verified,
                  verification_log=log).model_dump(), "error": None, "via": via}


def patch_with_cli(repo_url: str, sandbox_id: str, finding: dict) -> dict:
    """Up to two attempts. A rejected patch is discarded and the verifier's reason goes into the retry."""
    result, feedback = {}, None
    for attempt in (1, 2):
        prepare_workdir(repo_url)
        ok, out = run_codeaf_cli(fix_prompt(finding, feedback))
        result = finish(finding, sandbox_id, f"codeaf exec (CLI) on {llm.host()}, attempt {attempt}",
                        error=None if collect_diff().strip() else out[-600:])
        if result["verified"]:
            break
        feedback = result["verification_log"] or result.get("error") or "no patch produced"
    return result
