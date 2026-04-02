# RunbookAI

![CI](https://github.com/Pritom14/runbookai/actions/workflows/ci.yml/badge.svg)

Autonomous incident response agent. Gets paged → reads the runbook → acts → resolves or escalates.

## How it works

1. Alert arrives via PagerDuty webhook (or generic webhook)
2. Agent reads the runbook for that alert type (DB first, then YAML fallback)
3. In **Suggest Mode**: proposes each action, waits for human approval on high-risk steps
4. In **Autonomous Mode**: executes remediation steps directly
5. Resolved → posts summary + closes incident. Not resolved → escalates via Slack or email.

Every action is logged to the **AgentTrace** replay timeline — SREs see exactly what the agent did, which tools it called, how long each step took, and where it paused for approval.

## Suggest Mode (default)

The agent never executes a high-risk action (restart, scale, deploy) without explicit human approval. Low-risk diagnostics (`check_logs`, `http_check`) run immediately.

```
Alert received
    ↓
Agent diagnoses (check_logs, http_check) — runs immediately
    ↓
Agent proposes high-risk action
    ↓
POST /approvals/{id}/approve   ← human approves
    ↓
Agent executes + checks result
    ↓
Resolved or escalates
```

Flip to autonomous: set `SUGGEST_MODE=false` in `.env`.

## Quickstart

```bash
git clone https://github.com/Pritom14/runbookai
cd runbookai
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn runbookai.main:app --reload --port 7000
```

Fire a test incident:

```bash
curl -X POST http://localhost:7000/webhooks/generic \
  -H "Content-Type: application/json" \
  -d '{"alert_name":"High CPU on web-01","severity":"high","service":"web-01","details":"CPU at 95% for 10 minutes"}'
```

View the replay: `http://localhost:7000/incidents/{id}/replay/ui`

## LLM — Ollama (local, free)

RunbookAI runs fully locally via [Ollama](https://ollama.com). No API key required.

```bash
ollama pull qwen2.5:7b
```

Set in `.env`:
```
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen2.5:7b
```

Any Ollama model that supports tool calling works. `qwen2.5:7b` is recommended.

Optionally point at [Token0](https://github.com/Pritom14/token0) as an LLM proxy for cost tracking and model cascade.

## Runbooks

Runbooks are matched to alerts by substring on `alert_name`. Store them in the DB via the API or drop YAML files in `runbooks/`.

```bash
# Create a runbook via API
curl -X POST http://localhost:7000/runbooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "High CPU Runbook",
    "alert_pattern": "High CPU",
    "content": "1. Check top processes via check_logs\n2. If memory leak, restart_service\n3. If sustained, scale_service"
  }'
```

## Database

SQLite is the default (zero setup). Switch to PostgreSQL for production:

```bash
pip install runbookai[postgres]
```

```
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/runbookai
```

Agent conversation history (`messages_json`) is persisted to the DB after every turn — agent survives server restarts mid-incident.

## Escalation

Set either (or both) in `.env`:

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
ESCALATION_EMAIL=oncall@yourcompany.com
SMTP_HOST=smtp.gmail.com
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
| `POST /approvals/{id}/approve` | Approve proposed action |
| `POST /approvals/{id}/reject` | Reject proposed action |
| `POST /runbooks` | Create runbook |
| `GET /runbooks` | List runbooks |
| `DELETE /runbooks/{id}` | Delete runbook |

## License

Apache 2.0
