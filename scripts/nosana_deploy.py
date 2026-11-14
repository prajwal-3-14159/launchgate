#!/usr/bin/env python3
"""Deploy a vLLM OpenAI-compatible server on Nosana GPU and print its endpoint.

    python scripts/nosana_deploy.py markets          # list GPU markets (no key needed)
    python scripts/nosana_deploy.py deploy           # create + start + wait, prints LLM_BASE_URL=...
    python scripts/nosana_deploy.py status <id>
    python scripts/nosana_deploy.py stop <id>

Env / .env:
    NOSANA_API_KEY      required for deploy/status/stop (create at https://deploy.nosana.com -> Account)
    NOSANA_MARKET       market Solana address (default: NVIDIA 3090, 24 GB)
    LLM_MODEL           Hugging Face repo to serve (default below)
    NOSANA_TIMEOUT_MIN  job lifetime in MINUTES (default 120); SIMPLE strategy kills the job at timeout
    VLLM_IMAGE          default docker.io/vllm/vllm-openai:v0.10.2 (pre-cached on 3090/A5000/4090 markets)
    VLLM_TOOL_PARSER    override --tool-call-parser
    VLLM_MAX_MODEL_LEN  override --max-model-len (default 16384)
    NOSANA_API_BASE     default https://api.nosana.com

Verified 2026-11-14 against https://api.nosana.com/api/openapi.json and
github.com/nosana-ci/nosana-kit docs/api/*.md. Nothing here was run against a real key;
see docs/VERIFIED.md. Lines that could not be confirmed are marked `# UNVERIFIED:`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

API_BASE = os.environ.get("NOSANA_API_BASE", "https://api.nosana.com").rstrip("/")
MARKETS_URL = "https://api.nosana.com/api/markets/"  # public, no auth
DEFAULT_MARKET = "CA5pMpqkYFKtme7K31pNB1s62X2SdhEv1nN9RdxKCpuQ"  # NVIDIA 3090, 24 GB, ~$0.17/h
# UNVERIFIED: that this community AWQ quant loads in vLLM v0.10.2 within 24 GB at 16k context.
# Safe fallback: LLM_MODEL=Qwen/Qwen2.5-Coder-14B-Instruct-AWQ (hermes parser, tool calls less reliable).
DEFAULT_MODEL = "cyankiwi/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit"
DEFAULT_IMAGE = "docker.io/vllm/vllm-openai:v0.10.2"
PORT = 9000
BAD_STATES = {"ERROR", "STOPPING", "STOPPED", "INSUFFICIENT_FUNDS", "ARCHIVED"}


def tool_parser_for(model: str, image: str) -> str:
    if os.environ.get("VLLM_TOOL_PARSER"):
        return os.environ["VLLM_TOOL_PARSER"]
    m = model.lower()
    if "qwen3-coder" in m:
        # Registered as `qwen3_coder` in vLLM v0.10.2; current vLLM docs name it `qwen3_xml`.
        # UNVERIFIED: which vLLM release renamed it -- set VLLM_TOOL_PARSER if you change VLLM_IMAGE.
        return "qwen3_coder" if "v0.10" in image or "v0.9" in image else "qwen3_xml"
    return "hermes"  # vLLM docs: Qwen2.5-* use hermes


def api(method: str, path: str, **kw) -> dict:
    key = os.environ.get("NOSANA_API_KEY")
    if not key:
        sys.exit("NOSANA_API_KEY is not set (env or .env)")
    r = requests.request(
        method, f"{API_BASE}{path}", headers={"Authorization": f"Bearer {key}"}, timeout=60, **kw
    )
    if not r.ok:
        sys.exit(f"{method} {path} -> HTTP {r.status_code}: {r.text[:800]}")
    return r.json() if r.content else {}


def job_definition(model: str, image: str) -> dict:
    cmd = [  # image ENTRYPOINT is already the OpenAI api_server, so cmd is flags only
        "--model", model,
        "--port", str(PORT),
        "--max-model-len", os.environ.get("VLLM_MAX_MODEL_LEN", "16384"),
        "--gpu-memory-utilization", "0.92",
        "--enable-auto-tool-choice",
        "--tool-call-parser", tool_parser_for(model, image),
    ]
    return {
        "version": "0.1",
        "type": "container",
        "meta": {"trigger": "api"},  # server overwrites this with "deployment-manager"
        "ops": [{
            "type": "container/run",
            "id": "vllm",
            "args": {"image": image, "gpu": True, "cmd": cmd, "expose": PORT},
        }],
    }


def endpoint_of(dep: dict) -> tuple[str | None, bool]:
    eps = dep.get("endpoints") or []
    if not eps:
        return None, False
    return (eps[0].get("url") or "").rstrip("/") or None, bool(eps[0].get("online"))


def cmd_deploy(a: argparse.Namespace) -> None:
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    image = os.environ.get("VLLM_IMAGE") or DEFAULT_IMAGE
    market = os.environ.get("NOSANA_MARKET") or DEFAULT_MARKET
    body = {
        "name": a.name,
        "market": market,
        "timeout": int(os.environ.get("NOSANA_TIMEOUT_MIN", "120")),  # minutes
        "replicas": 1,
        "strategy": "SIMPLE",
        "job_definition": job_definition(model, image),
    }
    if a.dry_run:
        print(json.dumps(body, indent=2))
        return

    dep = api("POST", "/deployments/create", json=body)
    dep_id = dep["id"]
    print(f"deployment id: {dep_id}  (status {dep.get('status')})", flush=True)
    api("POST", f"/deployments/{dep_id}/start")  # created in DRAFT; start has no body

    deadline = time.time() + a.wait * 60
    url, last = None, None
    while time.time() < deadline:
        dep = api("GET", f"/deployments/{dep_id}")
        status = dep.get("status")
        url, online = endpoint_of(dep)
        if (status, online) != last:
            print(f"  status={status} endpoint_online={online}", flush=True)
            last = (status, online)
        if status in BAD_STATES:
            sys.exit(f"deployment entered {status}; see: {sys.argv[0]} status {dep_id}")
        if status == "RUNNING" and url:
            break
        time.sleep(10)
    else:
        sys.exit(f"timed out waiting for RUNNING; deployment {dep_id} is still up -- stop it if unwanted")

    print(f"\nLLM_BASE_URL={url}/v1\nLLM_MODEL={model}\n", flush=True)
    print("waiting for vLLM to load weights (typically 5-15 min) ...", flush=True)
    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/v1/models", timeout=15)
            if r.ok and r.json().get("data"):
                print("ready, serving:", [m.get("id") for m in r.json()["data"]])
                return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(15)
    sys.exit(f"endpoint did not answer /v1/models within {a.wait} min; check: {sys.argv[0]} status {dep_id}")


def cmd_status(a: argparse.Namespace) -> None:
    dep = api("GET", f"/deployments/{a.id}")
    url, online = endpoint_of(dep)
    print(f"status:   {dep.get('status')}\nendpoint: {url} (online={online})")
    if url:
        print(f"LLM_BASE_URL={url}/v1")
    if a.verbose:
        print(json.dumps(dep, indent=2))
        print(json.dumps(api("GET", f"/deployments/{a.id}/jobs"), indent=2))


def cmd_stop(a: argparse.Namespace) -> None:
    print(api("POST", f"/deployments/{a.id}/stop"))  # -> {"status": "STOPPING", ...}


def cmd_markets(a: argparse.Namespace) -> None:
    r = requests.get(MARKETS_URL, timeout=30)
    r.raise_for_status()
    rows = []
    for m in r.json():
        meta = {d.get("key"): d.get("value") for d in (m.get("metadata") or []) if isinstance(d, dict)}
        vram = str(meta.get("vram") or "")
        gb = int("".join(c for c in vram if c.isdigit()) or 0)
        if gb < a.min_vram and not a.all:
            continue
        vllm = [i.rsplit(":", 1)[-1] for i in (m.get("required_images") or []) if "vllm-openai" in i]
        rows.append((float(m.get("usd_reward_per_hour") or 0), m.get("name"), vram, m.get("type"),
                     m.get("address"), ",".join(vllm)))
    print(f"{'$/h':>7}  {'name':<24} {'vram':<6} {'type':<10} {'address':<45} cached vLLM tags")
    for p, name, vram, typ, addr, vllm in sorted(rows, key=lambda x: x[0]):
        print(f"{p:7.4f}  {str(name):<24} {vram:<6} {str(typ):<10} {str(addr):<45} {vllm}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy", help="create + start + wait for endpoint and /v1/models")
    d.add_argument("--name", default="launchgate-vllm")
    d.add_argument("--wait", type=int, default=30, help="minutes to wait overall (default 30)")
    d.add_argument("--dry-run", action="store_true", help="print the create body and exit")
    d.set_defaults(fn=cmd_deploy)
    s = sub.add_parser("status", help="show status and endpoint")
    s.add_argument("id")
    s.add_argument("-v", "--verbose", action="store_true", help="dump full deployment + jobs JSON")
    s.set_defaults(fn=cmd_status)
    t = sub.add_parser("stop", help="stop a deployment (stops billing)")
    t.add_argument("id")
    t.set_defaults(fn=cmd_stop)
    m = sub.add_parser("markets", help="list GPU markets (public, no key)")
    m.add_argument("--min-vram", type=int, default=24, help="minimum VRAM in GB (default 24)")
    m.add_argument("--all", action="store_true", help="include markets without VRAM metadata")
    m.set_defaults(fn=cmd_markets)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
