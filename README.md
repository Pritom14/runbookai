# RunbookAI

![CI](https://github.com/Pritom14/runbookai/actions/workflows/ci.yml/badge.svg)

Autonomous incident response agent. Gets paged → reads the runbook → acts → resolves or escalates.

No more 3am pages for problems your runbook already solves.

## Business Use Case

### The problem

Most production incidents on a given team are not novel. A service OOMs, a disk fills up, a DB connection pool leaks, a fan fails on a physical host — the runbook for each is already written down. What's expensive is the execution: someone gets paged, wakes up, VPNs in, SSHes to a box, runs the same handful of diagnostic commands, applies the same fix, and — if it worked — writes a postmortem the next day reconstructing what they just did from memory and Slack scrollback. That cycle is toil: it drives on-call burnout and attrition, it caps MTTR at "however fast a human can wake up and orient," and it's inconsistent because no two engineers run a runbook identically under pressure.

### Who it's for

SRE, DevOps, and platform teams that operate their own servers, Kubernetes workloads, databases, or on-prem hardware and already maintain runbooks (in a wiki, in PagerDuty, or just in a senior engineer's head), but still execute them by hand. It fits best where the incident classes are recurring and well-understood — OOM crashes, disk pressure, DB connection leaks, hardware thermal events — since RunbookAI automates *running* the runbook, not diagnosing incidents nobody has seen before.

### How it's different from what teams use today

- **PagerDuty / Opsgenie** route and alert; they don't touch the box or run the fix.
- **incident.io / Rootly** coordinate humans and help write postmortems; they don't execute remediation either.
- **A runbook in a wiki** is only as good as whether the on-call engineer reads it correctly at 3am.

RunbookAI's premise is that the runbook itself should run, not just be read.

### Value delivered

- **Lower MTTR** — diagnosis and remediation happen in seconds instead of the 10-20 minutes it takes a human to wake up, connect, and orient.
- **Less on-call toil** — low-risk diagnostics (`check_logs`, `check_disk`, `query_metrics`, …) run with no human involved at all; only genuinely risky actions (restart, delete, scale) interrupt someone, and only for a one-tap approval, not manual execution.
- **Consistent execution** — the same runbook runs the same way every time, removing the variance between a fresh engineer and a five-year veteran.
- **Institutional memory, not just automation** — regression detection means that if the same service fails again within 6 hours of a prior fix, the agent is told "you already tried that" and digs for root cause instead of blindly repeating a fix that didn't stick — encoding a lesson a tired human might otherwise forget.
- **Free postmortems** — a postmortem draft (timeline, actions taken, regression analysis, follow-ups) is generated automatically from the AgentTrace, eliminating the ~2 hours typically spent reconstructing an incident after the fact.
- **Auditability and trust** — every tool call and decision is logged and replayable, so a team can adopt autonomy incrementally via Suggest Mode before trusting the agent to act alone.

### Business model

- **Shipped today: self-hosted, open source (Apache 2.0).** Runs entirely on the operator's own infrastructure, including the LLM (local via Ollama by default), so no incident data has to leave the customer's network and there's no SaaS fee. This is what the current MVP submission demonstrates — see below.
- **In progress: managed cloud service.** The architecture (`docs/CLOUD_ARCHITECTURE.md`) has customers run a lightweight agent inside their own VPC that connects outbound to a RunbookAI-hosted control plane over SSE, so the vendor never gets direct SSH access to customer infrastructure — only the customer's own agent does. The intended monetization is tiered, usage-based pricing (by incidents and tool calls per month — see `docs/PRICING_MODELS_TODO.md`), with integrations like Slack/PagerDuty and analytics gated to paid tiers. Pricing itself is explicitly deferred pending customer interviews and market validation — the database/API foundation exists, but billing is not yet implemented or live.

## HYKR MVP Review

This submission demonstrates RunbookAI through a local Docker chaos demo: the API receives an alert, matches a runbook, runs diagnostics/remediation against a sandbox target, and shows the full AgentTrace replay.

- Repository: https://github.com/Pritom14/runbookai
- Technical submission notes: [SUBMISSION.md](SUBMISSION.md)
- Demo dashboard after local startup: `http://localhost:7000/static/chaos-board.html`
- Replay UI after an incident: `http://localhost:7000/incidents/{incident_id}/replay/ui`

Reviewer quickstart:

```bash
git clone https://github.com/Pritom14/runbookai
cd runbookai
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
bash demo/chaos/run.sh
```

By default this runs one representative hardware incident. Pass a scenario name such as `"CPU spike"` to run a different scripted incident.

Note: `demo/chaos/keys/` contains auto-generated throwaway SSH keys for the local Docker demo container only. They do not grant access to any external system and are excluded from future commits. Run `demo/chaos/setup.sh` on a fresh clone to regenerate them.

## How it works

1. Alert arrives via PagerDuty or generic webhook
2. Agent matches the runbook for that alert type
3. In **Suggest Mode** (default): runs diagnostics immediately, pauses for human approval before any destructive action
4. In **Autonomous Mode**: executes the full runbook without interruption
5. Resolved → posts summary. Not resolved → escalates via Slack or email with a precise root cause

Every action is logged to the **AgentTrace** replay timeline — see exactly what the agent did, which tools it called, and where it paused.

**Regression detection:** if the same service alerts again within 6 hours of a prior remediation, the agent is warned not to repeat the last fix — it digs deeper to find the root cause.

After resolution, a **postmortem draft** is auto-generated from the trace: full timeline, actions taken, regression analysis, and recommended follow-ups.

## Quickstart

**Prerequisites:** Python 3.11+, [Ollama](https://ollama.com)

```bash
git clone https://github.com/Pritom14/runbookai
cd runbookai
python -m venv .venv && source .venv/bin/activate
pip install -e .
ollama pull qwen2.5:7b
cp .env.example .env
uvicorn runbookai.main:app --port 7000
```

Fire a test alert:

```bash
curl -X POST http://localhost:7000/webhooks/generic \
  -H "Content-Type: application/json" \
  -d '{"alert_name":"High CPU on web-01","severity":"high","service":"web-01","details":"CPU at 95%","host":"web-01"}'
```

View the replay: `http://localhost:7000/incidents/{id}/replay/ui`

## Register SSH credentials

The agent connects to your servers via SSH to run diagnostics and remediations. Register credentials per host:

```bash
curl -X POST http://localhost:7000/api/hosts \
  -H "Content-Type: application/json" \
  -d '{
    "hostname": "web-01",
    "username": "ubuntu",
    "private_key_pem": "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
    "port": 22
  }'
```

Or set global defaults in `.env` for a single-server setup:

```
SSH_DEFAULT_USERNAME=ubuntu
SSH_PRIVATE_KEY_PATH=/home/deploy/.ssh/id_rsa
```

List registered hosts: `GET /api/hosts`

## Suggest Mode (default)

High-risk actions (restart, clear disk) always pause for human approval. Diagnostics run immediately.

```
Alert → check_logs, http_check, check_disk  ← runs immediately
       ↓
       Agent proposes: restart_service
       ↓
       POST /approvals/{id}/approve          ← you approve
       ↓
       Agent restarts + verifies
       ↓
       Resolved
```

Switch to autonomous: `SUGGEST_MODE=false` in `.env`.

## Tools

| Tool | Risk | What it does |
|---|---|---|
| `http_check` | Low | HTTP health check — status code, latency |
| `check_logs` | Low | Tail service logs via journalctl |
| `check_disk` | Low | Disk usage per mount, flags ≥80% critical |
| `check_processes` | Low | Is the process running? PID, CPU%, mem% |
| `query_metrics` | Low | CPU usage, memory, load average |
| `run_db_check` | Low | Postgres: active connections, idle-in-tx, locks |
| `restart_service` | **High** | systemctl restart + verify |
| `clear_disk` | **High** | Delete old files under /var/log or /tmp |
| `scale_service` | **High** | Scale a Kubernetes deployment |

High-risk tools always require approval in Suggest Mode.

## Hardware Integration

RunbookAI polls physical server sensors via IPMI using `runbookai/agent/ipmi_poller.py`. The daemon polls CPU temperature, fan speed, power draw, and disk SMART status every 60 seconds. When thresholds are exceeded (CPU temp >80°C, fan speed <500 RPM), it fires an alert to `/webhooks/hardware`, which triggers the full autonomous incident response loop.

Start the poller:
```bash
IPMI_HOST=192.168.1.10 IPMI_USER=admin IPMI_PASSWORD=secret python -m runbookai.agent.ipmi_poller
```

## Runbooks

Runbooks are matched to alerts by substring on `alert_name`. The agent reads the matching runbook before acting.

Store runbooks in the DB:

```bash
curl -X POST http://localhost:7000/runbooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Payment Service 503",
    "alert_pattern": "payment-service 503",
    "content": "1. http_check the health endpoint\n2. check_processes for payment-service\n3. check_logs for OOM or errors\n4. restart_service if process is dead\n5. http_check again to verify"
  }'
```

Or drop YAML files in `runbooks/`. Two demo runbooks are included:
- `runbooks/payment-service-503.yaml` — OOM crash → restart → verify
- `runbooks/checkout-latency.yaml` — DB connection leak → diagnose → escalate with root cause

## LLM

RunbookAI works with any OpenAI-compatible endpoint. Three env vars control it:

```
LLM_BASE_URL=...
LLM_MODEL=...
LLM_API_KEY=...
```

**Ollama (default — local, no API key, no cost):**

```bash
ollama pull qwen2.5:7b
```
```
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_API_KEY=ollama
```

**OpenAI:**
```
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...
```

**Anthropic:**
```
LLM_BASE_URL=https://api.anthropic.com/v1
LLM_MODEL=claude-sonnet-4-6
LLM_API_KEY=sk-ant-...
```

**Groq (fast + cheap, excellent tool calling):**
```
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
LLM_API_KEY=gsk_...
```

The model must support tool/function calling. `qwen2.5:7b` works well locally. For large, complex codebases or multi-service incidents, `gpt-4o` or `claude-sonnet-4-6` will produce more reliable reasoning.

## Database

SQLite by default (zero setup). Switch to PostgreSQL for production:

```bash
pip install ".[postgres]"
```

```
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/runbookai
```

Agent conversation history is persisted after every turn — survives server restarts mid-incident.

## Slack notifications

RunbookAI posts rich Slack messages at every incident lifecycle event:

| Event | What's sent |
|-------|-------------|
| Incident starts | Alert name, service, severity, link to replay UI |
| Approval required | Tool name, rationale, curl command to approve |
| Approval granted/rejected | Confirmation with tool name |
| Incident resolved | Duration, summary, link to postmortem |
| Incident escalated | Reason, incident link |

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

## API

| Endpoint | Description |
|---|---|
| `POST /webhooks/pagerduty` | Receive PagerDuty alert |
| `POST /webhooks/generic` | Receive generic JSON alert |
| `POST /webhooks/hardware` | Receive hardware sensor alert (IPMI, thermal, fan) |
| `GET /incidents` | List all incidents |
| `GET /incidents/{id}` | Incident detail |
| `GET /incidents/{id}/replay` | AgentTrace timeline (JSON) |
| `GET /incidents/{id}/replay/ui` | AgentTrace replay UI |
| `GET /incidents/{id}/postmortem` | Auto-generated postmortem markdown |
| `GET /incidents/analysis` | Pattern summary — MTTR, top tools, regressions per service |
| `GET /incidents/compare` | Side-by-side diff of two incident traces |
| `POST /approvals/{id}/approve` | Approve a proposed action |
| `POST /approvals/{id}/reject` | Reject a proposed action |
| `POST /runbooks` | Create runbook |
| `GET /runbooks` | List runbooks |
| `DELETE /runbooks/{id}` | Delete runbook |
| `POST /api/hosts` | Register SSH credentials for a host |
| `GET /api/hosts` | List registered hosts |
| `DELETE /api/hosts/{hostname}` | Remove host credentials |

## License

Apache 2.0
