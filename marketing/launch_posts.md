# RunbookAI Launch Posts

## 1. dev.to — Technical Article (~400 words)

**Title:** Tired of Being Paged at 3am? Let Your AI Handle the Runbook

**Content:**

```markdown
# Tired of Being Paged at 3am? Let Your AI Handle the Runbook

When that alert fires at 3:14am on Sunday, you know the drill: VPN in, SSH to the server, check logs, maybe restart the service, page escalates to someone else. You've probably done this 100 times.

What if the runbook executed itself?

## Meet RunbookAI

RunbookAI is an open-source autonomous incident response agent. Connect it to PagerDuty, fire a webhook at it, and it reads your runbook, diagnoses the problem, and acts—without paging a human first.

### How It Works

1. **Alert fires** → RunbookAI reads the runbook
2. **Diagnosis** → runs tools: check_logs, http_check, run_db_check, query_metrics, check_disk, check_processes
3. **Remediation** → executes: restart_service, clear_disk, scale_service
4. **Resolves or escalates** → full summary, no human was involved

```bash
git clone https://github.com/Pritom14/runbookai
cd runbookai
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run with local LLM (no API keys)
ollama pull qwen2.5:7b
DEMO_MODE=true uvicorn runbookai.main:app --port 7000
python demo/run_demo.py regression
```

### The Game-Changer: Regression Detection

Here's the real magic. Your service crashed 2 hours ago. RunbookAI restarted it. But if it crashes *again* within 6 hours, the agent is warned: "Don't just restart again—you did that before. Dig deeper."

Instead of blindly running the same remediation, it:
- Checks for new logs
- Queries recent metrics changes
- Looks for disk space issues, process hangs, or configuration drift
- Suggests a root cause before acting

This turns "fix the symptom" into "understand the problem."

### Suggest Mode

High-risk actions (service restart, disk cleanup, scale-up) pause with a 5-second countdown for human approval. You stay in control while the agent handles the grunt work.

### Auto-Generated Postmortem

After every resolved incident, hit `GET /incidents/{id}/postmortem` and get a ready-to-share markdown document: full timeline, actions taken, regression analysis, duration, and a recommendations checklist. Two hours of postmortem work, done automatically.

### Slack Lifecycle Notifications

Set `SLACK_WEBHOOK_URL` and RunbookAI posts a rich message at every stage: incident started, approval required (with the curl command to approve), resolved with duration, escalated with reason. Your Slack channel becomes your incident dashboard.

### AgentTrace Replay UI

Every tool call, every decision, every second of the remediation is logged. Open the browser, replay the entire incident timeline. Understand what the agent decided and why.

### Why Open Source?

Incident response is deeply custom—every company's runbooks, tools, and risk tolerance differ. We ship the core (diagnosis + remediation) free and self-hosted. No vendor lock-in, no SaaS fee, no pinging external APIs.

### No API Keys Needed

Runs on Ollama locally. qwen2.5:7b is small, fast, and good enough for runbook reasoning. Everything stays on your infrastructure.

---

**GitHub:** https://github.com/Pritom14/runbookai

Try it now. Fire a demo alert. See regression detection in action. Fork, extend, and own your incident response.
```

---

## 2. r/devops — Conversational Post (Max 200 words)

**Title:** RunbookAI – Autonomous incident response that reads your runbook and acts

**Content:**

```
Getting paged at 3am for the same alert you've seen 20 times? RunbookAI reads your runbook and handles it—no human required (until it gets scary).

It's like having a reliable on-call engineer who knows your runbook by heart.

**How it works:**
Alert fires → agent reads the runbook → diagnoses (logs, metrics, disk, processes) → remediates (restart, cleanup, scale) → resolves or escalates.

**The killer feature:** If the same service fails again within 6 hours, the agent doesn't just restart blindly. It's warned "you did this before" and digs deeper—new logs, metric changes, disk issues.

**Setup is 2 minutes:**
```
git clone https://github.com/Pritom14/runbookai
cd runbookai && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ollama pull qwen2.5:7b
DEMO_MODE=true uvicorn runbookai.main:app --port 7000
python demo/run_demo.py regression
```

Self-hosted, open source, runs on local LLM (no API keys). Everything stays on your infrastructure.

**Postmortem auto-generated:** After resolution, `GET /incidents/{id}/postmortem` returns a full markdown doc — timeline, root cause, recommendations. No manual postmortem writing.

**Slack notifications:** Set a webhook URL and get rich messages at every stage — incident start, approval needed (with the curl command), resolved with duration.

**Full replay:** AgentTrace UI shows every decision the agent made — useful for postmortems and learning.

Would love your feedback. Especially from folks running incident response tooling.

GitHub: https://github.com/Pritom14/runbookai
```

---

## 3. r/sre — SRE-Focused Post (Max 200 words)

**Title:** RunbookAI – Autonomous runbook execution with regression detection

**Content:**

```
SREs: we all know runbooks work great until the 3am follow-up incident on the same service. Different root cause, same symptoms, but the human on-call runs the same fix again.

RunbookAI executes your runbook autonomously and learns from history.

**Key features for reliability:**

**Regression Detection:** If service X failed 2 hours ago and the runbook restarted it, but it fails again now, the agent is warned. Instead of re-running the same remediation (high MTTR), it digs deeper—new logs, metric anomalies, disk/CPU changes. Root cause hunting before acting again.

**AgentTrace Replay:** Every tool call, every decision logged. Open the browser post-incident and replay the timeline. Understand what the agent decided and trace why—critical for postmortems and blameless reviews.

**Suggest Mode:** High-risk actions (restart, scale, delete) pause with human approval. You control the blast radius while the agent handles grunt work.

**Reduce MTTR:** Agent diagnoses in seconds, executes faster than a human waking up. Fewer false escalations through smarter reasoning.

**Auto postmortem:** After resolution, a blameless postmortem is generated from the AgentTrace — full timeline, actions taken, regression analysis. No more 2-hour postmortem meetings reconstructing what happened.

**Slack integration:** Rich Block Kit notifications at every incident lifecycle event — started, approval needed (with approve command), resolved with MTTR, escalated with reason.

Self-hosted on Ollama (qwen2.5:7b). No API keys, no vendor lock-in.

**Try it:**
```
git clone https://github.com/Pritom14/runbookai
cd runbookai && pip install -e ".[dev]"
ollama pull qwen2.5:7b && DEMO_MODE=true uvicorn runbookai.main:app --port 7000
python demo/run_demo.py regression
```

Feedback welcome. Especially from folks running SRE orgs at scale.

https://github.com/Pritom14/runbookai
```

---

## 4. r/LocalLLaMA — Local Model Focused (Max 150 words)

**Title:** RunbookAI – Autonomous incident response running 100% on Ollama (qwen2.5:7b, no API keys)

**Content:**

```
Incident response that runs entirely on local models. No OpenAI, no Claude API, no vendor dependencies.

**Tool-calling setup:**
- Define runbook in YAML
- Agent reads it, chains tool calls: check_logs() → query_metrics() → restart_service()
- Runs on qwen2.5:7b via Ollama

**Why it works:**
Runbook reasoning is lightweight. You don't need GPT-4. A small local model reasoning through structured diagnostics beats an expensive API.

**Setup (2 min):**
```
git clone https://github.com/Pritom14/runbookai
cd runbookai && pip install -e ".[dev]"
ollama pull qwen2.5:7b
DEMO_MODE=true uvicorn runbookai.main:app --port 7000
python demo/run_demo.py regression
```

No credentials, no rate limits, everything stays local.

Plus: regression detection (if same service fails again, dig deeper, don't repeat fix).

Try the demo. Feedback appreciated.

https://github.com/Pritom14/runbookai
```

---

## 5. r/selfhosted — Self-Hosted & Open-Source Focused (Max 150 words)

**Title:** RunbookAI – Self-hosted autonomous incident response, zero vendor lock-in

**Content:**

```
Your incident response stays on your infrastructure. No SaaS, no APIs, no third-party incident.io/PagerDuty dependency (integrates with them, but runs locally).

**Stack:**
- SQLite by default (zero setup), Postgres optional
- Docker Compose available for quick spin-up
- Open-source, fully hackable
- Runs on local Ollama (qwen2.5:7b)

**How it works:**
Alert fires → agent reads your runbook → diagnoses (logs, metrics, disk, processes) → remediates (restart, scale, cleanup) → resolves or escalates.

Features:
- AgentTrace replay UI (full audit trail)
- Regression detection (if same alert fires again, don't repeat the last fix—dig deeper)
- Suggest Mode (high-risk actions pause for approval)

**Quick start:**
```
git clone https://github.com/Pritom14/runbookai
cd runbookai && pip install -e ".[dev]"
ollama pull qwen2.5:7b
DEMO_MODE=true uvicorn runbookai.main:app --port 7000
python demo/run_demo.py regression
```

Or Docker Compose in repo.

Self-hosted incident response. No surprises. Your data, your rules.

https://github.com/Pritom14/runbookai
```

---

## Posting Schedule (Suggested)

**Day 1 (Launch Day):**
- r/devops (morning, high traffic)
- r/sre (morning + 2h later)
- r/LocalLLaMA (morning + 4h later)

**Day 2 (+24h):**
- r/selfhosted (morning)

**Day 3 (+48h):**
- dev.to article (cross-share to Twitter/LinkedIn)

---

## Notes

- All posts link to GitHub: https://github.com/Pritom14/runbookai
- Installation commands are identical across all posts (for consistency)
- dev.to post is the only "long form"—other platforms are conversational/scannable
- Regression detection is highlighted because it's the unique differentiator vs. generic incident tools
- No marketing fluff—lead with the problem (being paged), show the solution, include a runnable demo
