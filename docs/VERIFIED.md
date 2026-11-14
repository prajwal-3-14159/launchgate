# VERIFIED — docs check, 14 Nov 2026

Checked against public docs, GitHub source and the installed SDK. **Nothing was run against a real service**: no Nosana key, no `af server`, no `codeaf` binary. The only live calls were unauthenticated GETs (Nosana markets listing, OpenAPI spec, Hugging Face repo metadata).

Abbreviations:
- `SDK/` = `.venv/lib/python3.13/site-packages/agentfield/` (agentfield 0.1.145)
- `AF/` = `https://github.com/Agent-Field/agentfield/blob/main/` (control plane read at `main`, not a pinned release)
- `CAF/` = `https://raw.githubusercontent.com/Agent-Field/CodeAF/v0.8.2/`
- `NOS-SPEC` = `https://api.nosana.com/api/openapi.json`
- `NOS-KIT/` = `https://raw.githubusercontent.com/nosana-ci/nosana-kit/main/`

## 1. Nosana + vLLM

| Claim | Verdict | Detail | Source |
|---|---|---|---|
| Auth `Authorization: Bearer <NOSANA_API_KEY>` | Confirmed | Key format `nos_...`, created at deploy.nosana.com → Account | https://learn.nosana.com/api/intro.html |
| Base `https://dashboard.k8s.prd.nos.ci/api` | **Wrong (outdated)** | Documented base is `https://api.nosana.com`, with no `/api` prefix on deployment paths. The old host still answers 401, so it may work, but it is undocumented. | `NOS-KIT/docs/api/intro.md` |
| `POST /deployments/create` | Confirmed | Required: `name`, `replicas`, `timeout`, `strategy`, `job_definition`. Optional: `market`, `autostart`, `idempotency_key`, `requirements`. | NOS-SPEC (`DeploymentManagerDeploymentCreateBody`) |
| `timeout` | Confirmed, unit is **minutes** | "Timeout in minutes, must be at least 1 minute" | NOS-SPEC |
| `market` | Confirmed | Solana address. If omitted, the cheapest PREMIUM market matching `requirements` is used. | NOS-SPEC; `NOS-KIT/docs/deployments/options.md` |
| `replicas: 1`, `strategy: "SIMPLE"` | Confirmed | Strategy enum: `SIMPLE`, `SIMPLE-EXTEND`, `SCHEDULED`, `INFINITE`. SIMPLE stops at `timeout`. | NOS-SPEC |
| Inline `job_definition` | Confirmed | Server pins it to IPFS. `meta.trigger` is overwritten with `"deployment-manager"`. | https://github.com/nosana-ci/nosana-deployment-manager (`deploymentCreate.factory.ts`) |
| `cmd` list, `gpu: true`, `expose: 9000` | Confirmed | `expose` may also be `[{port, type, health_checks}]` | NOS-SPEC (`DeploymentManagerJobDefinition`) |
| `POST /deployments/{id}/start` | Confirmed | No body. Deployments are created in `DRAFT`. `"autostart": true` on create is the alternative. | `NOS-KIT/docs/api/create-deployments.md` |
| Poll `GET /deployments/{id}` until running | Confirmed | Status enum: `DRAFT`, `ERROR`, `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`, `INSUFFICIENT_FUNDS`, `ARCHIVED` | NOS-SPEC |
| Endpoint URL in the response | Confirmed | `endpoints[i].url`, with `endpoints[i].online` as the readiness flag. It is a hash subdomain; do not derive it from the id. | NOS-SPEC; `endpoint.schema.ts` in nosana-deployment-manager |
| `POST /deployments/{id}/stop` | Confirmed | Returns `{"status":"STOPPING",...}`. Also `POST .../archive`, `DELETE /deployments/{id}`. | `NOS-KIT/docs/api/manage-deployments.md` |
| Markets at `https://api.nosana.com/api/markets/` | Confirmed | Public, no auth. VRAM is in `metadata[{key:"vram"}]`, price in `usd_reward_per_hour`. | live GET; https://docs.nosana.com/llms.txt |
| Markets with ≥24 GB | Confirmed | NVIDIA 3090 `CA5pMpqkYFKtme7K31pNB1s62X2SdhEv1nN9RdxKCpuQ` ($0.1745/h), A5000 `4uBye3vJ1FAYukDdrvqQ36MZZZxqW3o8utWu8fyomRuN` ($0.1818/h), 4090 `97G9NnvBDQ2WpKu6fasoMsAKmfj63C9rhysJnkeWodAf` ($0.3636/h), A6000 48 GB `EjryZ6XEthz3z7nnLfjXBYafyn7VyHgChfbfM47LfAao` ($0.3636/h) | live GET (`scripts/nosana_deploy.py markets`) |
| Image `vllm/vllm-openai:latest` | **Wrong choice** | `latest` is not in any market's `required_images` (a 10.9 GB pull). `docker.io/vllm/vllm-openai:v0.10.2` is cached on the 3090, A5000 and 4090 markets. | live markets GET |
| `cmd` is flags only | Confirmed | v0.10.2 `ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]` | https://raw.githubusercontent.com/vllm-project/vllm/v0.10.2/docker/Dockerfile |
| `--enable-auto-tool-choice`, `--tool-call-parser` | Confirmed | | https://docs.vllm.ai/en/latest/features/tool_calling.html |
| `hermes` for Qwen2.5-Coder | Confirmed in vLLM docs, **unreliable in practice** | A third-party repo reports the Coder variant emits fenced JSON instead of `<tool_call>` tags, leaving `tool_calls` empty. Not in official docs. | vLLM page above; https://github.com/hanXen/vllm-qwen2.5-coder-tool-parser |
| Parser for Qwen3-Coder | Confirmed | `qwen3_coder` in v0.10.2; current vLLM docs call it `qwen3_xml` | https://raw.githubusercontent.com/vllm-project/vllm/v0.10.2/vllm/entrypoints/openai/tool_parsers/qwen3coder_tool_parser.py |
| Strongest coder in 24 GB | Recommendation | `cyankiwi/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit` (18.1 GB, community quant) with `qwen3_coder`. Official fallback: `Qwen/Qwen2.5-Coder-32B-Instruct-AWQ` (19.3 GB, context ~12k) or `...-14B-Instruct-AWQ` (10 GB, 32k context) with `hermes`. All repos exist and are ungated. | Hugging Face API |
| Extra header needed with API key | Could not confirm | Spec lists `x-user-id` for the wallet-signature path; docs' curl examples send only Bearer | NOS-SPEC |
| Qwen3-Coder quant loads in v0.10.2 at 16k context on 24 GB | Could not confirm | Not tested. On OOM set `VLLM_MAX_MODEL_LEN=8192` or use the A6000 market. | — |
| Cold start | Estimate only | About 5–15 min with the cached image (weights download 3–10 min, load 1–3 min); 10–30 min with `latest` | — |
| Nodes accept uncached images; live node availability; whether the 10% network fee is added to the listed price | Could not confirm | | — |

## 2. AgentField control plane

| Claim | Verdict | Detail | Source |
|---|---|---|---|
| Start with `af server` | Confirmed | API on `:8080`, UI at `http://localhost:8080/ui/`. Opens a browser unless `--open=false`. Port via `--port` or `AGENTFIELD_PORT`. | `AF/README.md` L179; `AF/control-plane/internal/cli/root.go` |
| Install | Confirmed (not run) | `curl -fsSL https://agentfield.ai/install.sh \| bash`. By default it also installs a skill into detected coding agents and a macOS tray with launchd auto-start; `bash -s -- --no-skill --no-tray` opts out. Docker: `docker run -p 8080:8080 agentfield/control-plane:latest`. | `AF/README.md` L42, L198; `AF/scripts/install.sh` L16-43 |
| SDK finds it via `AGENTFIELD_SERVER` | Confirmed | Order: `agentfield_server=` kwarg, `AGENTFIELD_SERVER`, `AGENTFIELD_SERVER_URL`, `http://localhost:8080` | `SDK/agent.py` L834-839 |
| `POST /api/v1/execute/{node}.{reasoner}` with `{"input": {...}}` | Confirmed | Only `Content-Type: application/json` needed unless the server has `AGENTFIELD_API_KEY` set (then `X-API-Key`). | `AF/control-plane/internal/server/routes_core.go` L118-120; `handlers/execute.go` L43-50 |
| Sync response shape | **Partly wrong** | Keys: `execution_id`, `run_id`, `status`, `result`, `error_message`, `error_details`, `duration_ms`, `finished_at`, `webhook_registered`. Return value is in **`result`**. There is no `error` key on a 200. | `handlers/execute.go` L60-70, L421-439 |
| Sync error shape | Not in draft | Non-200 with `{"error", "error_category", "status":"failed", "error_details"}`. Agent 5xx becomes 502, timeout becomes 504. | `handlers/execute_helpers.go` L547-603 |
| Sync timeout | Confirmed | Code default **90 s**. Set `AGENTFIELD_AGENT_CALL_TIMEOUT=600s` (or YAML `agentfield.execution_queue.agent_call_timeout`). The repo's sample config uses 1800s. | `AF/control-plane/internal/config/config.go` L340-345, L695 |
| Which timeout a fresh `af server` uses | Could not confirm | Depends on whether the installer writes a config file | — |
| Async `POST /api/v1/execute/async/{target}` | Confirmed | Same body. Returns 202 with `execution_id`, `run_id`, `workflow_id`, `status:"queued"`, `target`, `type`, `created_at`. | `routes_core.go` L125; `execute.go` L73-84 |
| Poll `GET /api/v1/executions/{id}` | Confirmed | Keys include `status`, `result`, **`error`** (not `error_message`), `started_at`, `completed_at`, `duration_ms`. Terminal statuses: `succeeded`, `failed`, `cancelled`, `timeout`. Others: `pending`, `queued`, `waiting`, `running`, `paused`. | `execute.go` L87-107; `AF/control-plane/pkg/types/status.go` |
| `app.call("node.reasoner", **kwargs)` returns the callee's dict | Confirmed, with a trap | The SDK also unwraps a top-level `"result"` key in the returned dict, so do not name a top-level return key `result`. Needs the control plane up, even for same-agent calls. Raises `ExecutionFailedError` / `ExecutionTimeoutError`. | `SDK/agent.py` L4845-4847, L4654-4679 |
| `app.run()` vs `app.serve(port=...)` | Both work | `run(**kw)` adds CLI mode (`call`/`list`/`shell`/`help` as argv[1]) and otherwise calls `serve(**kw)`. Use `app.run(port=8001)`. | `SDK/agent.py` L5724-5792 |
| `python app.py --port N` | **Wrong** | Only mentioned in a docstring; nothing parses it. Use `port=` or env `PORT`. Default 8001; a busy port is silently replaced by a free one. | `SDK/agent_server.py` L713-830 |
| Reasoner kwargs = keys of `input` | Confirmed | | `SDK/agent.py` L2195-2275 |
| Webhook callback payload shape | Could not confirm | Request key is `"webhook": {"url","secret","headers"}` | `execute.go` L46 |

## 3. CodeAF harness

| Claim | Verdict | Detail | Source |
|---|---|---|---|
| `CODEAF_BASE_URL` | Confirmed | Must include `/v1`; CodeAF appends `/chat/completions`. Default is OpenRouter. | `CAF/internal/config/config.go:511`; `CAF/internal/provider/client.go:2543` |
| `OPENAI_API_KEY` | Confirmed, with two catches | `OPENROUTER_API_KEY` outranks it and would be sent to our endpoint, so unset it. The SDK refuses to run without a non-empty key, so set a dummy even if vLLM has no auth. | `CAF/docs/GUIDE.md:298-302`; `SDK/harness/providers/codeaf.py:150-218` |
| `CODEAF_MODEL` | Confirmed | `--model` wins over it, and the SDK always passes `--model` (falling back to `CODEAF_MODEL`, then `~deepseek/deepseek-v4-flash-latest`). | `CAF/docs/HEADLESS.md` §1; `SDK/harness/providers/codeaf.py:380-385` |
| `codeaf exec` flags `--json --dir --model --max-turns --timeout` | Confirmed | `--max-turns` default 200. `--timeout` is a duration (`90s`, `15m`), default 15 min. Prompt is a positional arg or stdin. Also `--system`, `--token-budget` (220000), `--out`. | `CAF/docs/HEADLESS.md` §2 |
| Auto-approve flag on `exec` | **Wrong: none exists** | `exec` runs tools unattended and edits `--dir` in place | `CAF/docs/HEADLESS.md` §2 |
| JSON envelope | **Wrong field names** | Keys: `ok`, `stop`, `answer`, `files`, `error`, `spend_usd`, `tokens{in,out}`, `seconds`, `model`, `steps`, `run`, `calls`, `rounds`. No `result`, `is_error`, `num_turns` or `session_id`. | `CAF/docs/HEADLESS.md` §2 |
| Exit codes | Confirmed | 0 done; 1 error (could not run); 2 incomplete; 3 budget / turn-cap / deadline with partial work in `answer` | `CAF/docs/HEADLESS.md` §2 |
| Needs tool calling on the endpoint | Confirmed | Sends OpenAI `tools` with `tool_choice: "auto"`. No text-protocol fallback; on refusal it degrades to a tool-less answer that cannot read or edit files. | `CAF/internal/provider/endpoints.go:100-126, 451-497`; `CAF/internal/provider/toolless.go` |
| `Agent.harness(...)` command line | Confirmed | `codeaf exec --json --dir <project_dir or cwd> --model <model> --timeout <~1795s> [--max-turns N] [--system ...]`, prompt on stdin. `cwd=` becomes `--dir`. Child env is `os.environ` + `env=` + `CODEAF_HOME=~/.agentfield/codeaf`. `tools`, `permission_mode`, `max_budget_usd` are ignored. | `SDK/harness/providers/codeaf.py:398-430` |
| `HarnessResult.result` / `.parsed` | Confirmed | `.result` = envelope `answer` text. `.parsed` = validated Pydantic instance (raw unvalidated dict if `schema` is a dict), `None` without a schema. Budget / turn-cap stops with text count as success. | `SDK/harness/_result.py:57-84`; `SDK/harness/providers/codeaf.py:445-506` |
| `schema=` mechanism | Confirmed | No native structured output. The prompt tells the agent to write `<tmp>/.agentfield_output.json` under `cwd`; the SDK reads and validates it, then falls back to JSON extraction from the answer text, then re-runs codeaf up to 2 more times. | `SDK/harness/_runner.py:316-343, 448-649`; `SDK/harness/_schema.py` |
| `schema=` on a weak model | Confirmed | **No exception.** `is_error=True`, `parsed=None`, `failure_type=FailureType.SCHEMA`, after up to 3 full runs. The SDK's litellm repair call uses the bare model name and ignores `env=`, so it will most likely fail against vLLM. | `SDK/harness/_runner.py` |
| Version and download | Confirmed | Pinned `v0.8.2`, from `github.com/Agent-Field/CodeAF/releases/download/v0.8.2/codeaf-<os>-<arch>` to `~/.agentfield/bin/codeaf`, SHA-256 checked; darwin-arm64 supported. Override with `CODEAF_BIN`; disable with `AGENTFIELD_SKIP_CODEAF=1`. | `SDK/harness/_codeaf_install.py:15-148` |
| vLLM accepts CodeAF's OpenRouter-style extra body fields | Could not confirm | | — |
| Context handling for a model unknown to CodeAF's catalog | Could not confirm | `CODEAF_COMPLETION_RESERVE` defaults to 65536, larger than a 16k vLLM context; set it in `env=` if requests are rejected | — |
| vLLM's 400 on missing tool support triggers the tool-less ladder (vs exit 1) | Could not confirm | | — |
