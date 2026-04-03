# RunbookAI

![CI](https://github.com/Pritom14/runbookai/actions/workflows/ci.yml/badge.svg)

Autonomous incident response agent. Gets paged → reads the runbook → acts → resolves or escalates.

No more 3am pages for problems your runbook already solves.

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

Runs locally via Ollama — no API key, no cost.

```bash
ollama pull qwen2.5:7b
```

```
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen2.5:7b
```

Any Ollama model with tool-calling support works. `qwen2.5:7b` is recommended.

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

For email escalation:

```
ESCALATION_EMAIL=oncall@yourcompany.com
SMTP_HOST=smtp.gmail.com
SMTP_USER=you@gmail.com
SMTP_PASSWORD=...
```

## API

| Endpoint | Description |
|---|---|
| `POST /webhooks/pagerduty` | Receive PagerDuty alert |
| `POST /webhooks/generic` | Receive generic JSON alert |
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
