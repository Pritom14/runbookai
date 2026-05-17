# RunbookAI HYKR MVP Submission

## Summary

RunbookAI is an autonomous incident response agent. It receives alerts, matches the right runbook, runs diagnostics, takes approved or autonomous remediation actions, and records every step in a replayable incident timeline.

The MVP focuses on a realistic local chaos demo: a FastAPI control plane receives incidents, a Docker-based target service exposes SSH/BMC-style failure surfaces, and the agent executes runbook-driven remediation while the reviewer watches the dashboard and replay trace.

## Repository

GitHub: https://github.com/Pritom14/runbookai

## What The MVP Demonstrates

- Generic and hardware alert intake through webhook endpoints.
- Runbook matching from YAML and database-backed runbooks.
- Tool execution for diagnostics and remediation.
- Autonomous demo mode for end-to-end incident handling.
- BMC sensor simulation for hardware-style incidents.
- AgentTrace replay UI showing the exact sequence of decisions and tool calls.
- Post-incident summary and postmortem-oriented trace data.

## Architecture

RunbookAI is organized as a FastAPI application with a small incident-response engine behind it.

- `runbookai/main.py` starts the API, loads routers, serves static dashboards, initializes the database, and loads disk runbooks.
- `runbookai/api/` contains webhook, incident, replay, runbook, approval, host, BMC, customer, and dashboard routes.
- `runbookai/agent/` contains the agent, tool implementations, suggest-mode approval logic, cloud-agent pieces, and demo harness code.
- `runbookai/models.py` and `runbookai/database.py` define the SQLite/Postgres-backed persistence layer.
- `runbookai/static/chaos-board.html` is the main MVP demo dashboard.
- `runbookai/static/replay.html` is the incident replay UI.
- `demo/chaos/` contains the local Docker chaos target, demo runbooks, setup script, and scripted failure injection.

## Tech Stack

- Python 3.11+
- FastAPI
- SQLAlchemy async
- SQLite by default, PostgreSQL-compatible configuration
- Docker for the local chaos target
- SSH-based tool execution through demo credentials
- YAML runbooks
- OpenAI-compatible LLM configuration, with Ollama as the default local option
- Pytest and Ruff for verification

## Local MVP Demo

Prerequisites:

- Python 3.11+
- Docker running locally
- `curl`
- Optional but recommended: Ollama with `qwen2.5:7b`

Setup:

```bash
git clone https://github.com/Pritom14/runbookai
cd runbookai
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

If using Ollama:

```bash
ollama pull qwen2.5:7b
```

Run the default single-incident demo:

```bash
bash demo/chaos/run.sh
```

To run a different scripted incident, pass part of the scenario name:

```bash
bash demo/chaos/run.sh "CPU spike"
```

Open:

```text
http://localhost:7000/static/chaos-board.html
```

The demo script starts the target container, registers the demo SSH host, loads demo runbooks, starts the API if needed, opens the chaos board, injects one incident, and then cleans up the target container.

## Useful URLs

After the server is running:

- Health: `http://localhost:7000/health`
- Chaos board: `http://localhost:7000/static/chaos-board.html`
- Incidents: `http://localhost:7000/incidents`
- Replay UI: `http://localhost:7000/incidents/{incident_id}/replay/ui`
- Replay JSON: `http://localhost:7000/incidents/{incident_id}/replay`

## Verification

```bash
ruff check runbookai tests
pytest -q
```

## Demo-Only SSH Keys

`demo/chaos/keys/` contains auto-generated throwaway SSH keys for the local Docker demo container only. They do not grant access to any external system and are excluded from future commits. Run `demo/chaos/setup.sh` on a fresh clone to regenerate them.

## Known Limitations

- The submitted MVP is optimized for a deterministic local Docker demo rather than a hosted multi-tenant production deployment.
- The cloud/customer-agent architecture is partially implemented and documented, but the primary review path is the local chaos demo.
- Some infrastructure signals are simulated so reviewers can reproduce incidents without needing real production systems.
- The LLM provider must support tool/function-style calls for non-demo autonomous operation.
