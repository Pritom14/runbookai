# RunbookAI

Autonomous incident response agent. Gets paged → reads the runbook → acts → resolves or escalates.

## How it works

1. Alert arrives via PagerDuty webhook (or generic webhook)
2. Agent reads the runbook for that alert type
3. In **Suggest Mode**: proposes the next action, waits for human approval
4. In **Autonomous Mode**: executes remediation steps directly
5. Resolved → posts summary + closes incident. Not resolved → escalates with full context.

Every action is logged to the **incident replay** timeline (AgentTrace) — SREs see exactly what the agent did, which tools it called, where it hesitated.

## Suggest Mode (default)

Suggest Mode is the default and recommended starting point. The agent never executes an action without explicit human approval. This builds trust before you flip to autonomous mode.

```
Alert received
    ↓
Agent analyzes + proposes action
    ↓
POST /approvals/{action_id}/approve   ← human clicks approve
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
cp .env.example .env   # add your API keys
uvicorn runbookai.main:app --reload
```

## LLM routing via Token0

RunbookAI uses [Token0](https://github.com/Pritom14/token0) for all LLM calls. Simple incidents route to cheaper models automatically; complex cascading failures get the full model. No configuration needed.

## API

| Endpoint | Description |
|---|---|
| `POST /webhooks/pagerduty` | Receive PagerDuty alert |
| `POST /webhooks/generic` | Receive generic alert payload |
| `GET /incidents` | List all incidents |
| `GET /incidents/{id}` | Incident detail |
| `GET /incidents/{id}/replay` | Full AgentTrace timeline |
| `POST /approvals/{action_id}/approve` | Approve proposed action |
| `POST /approvals/{action_id}/reject` | Reject proposed action |
