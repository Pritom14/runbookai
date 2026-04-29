# Cloud Incident Routing Architecture

This document explains how RunbookAI cloud routes incidents to customer agents and orchestrates responses.

## Incident Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. INCIDENT ARRIVAL                                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Monitoring System (Datadog, Grafana, PagerDuty)                  │
│              ↓ (webhook with X-API-Key header)                    │
│       POST /webhooks/datadog                                       │
│       X-API-Key: sk_abc123...                                      │
│       Content-Type: application/json                              │
│       { "alert_name": "High CPU", "service": "api" }             │
│              ↓                                                    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ 2. WEBHOOK HANDLER                                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  a) Extract X-API-Key header                                       │
│     → Lookup customer_id from api_key                             │
│                                                                     │
│  b) Create Incident record with customer_id                        │
│     → INSERT incidents (id, customer_id, source, alert_name, ...) │
│                                                                     │
│  c) Call route_incident_to_customer()                             │
│     → Check if customer has online agent                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ 3. ROUTING DECISION                                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  SELECT Agent                                                       │
│  WHERE customer_id = <customer_id>                                │
│    AND status = 'online'                                          │
│    AND last_heartbeat > NOW - 2 minutes                           │
│                                                                     │
│         ┌──────────────────────┬──────────────────────┐           │
│         ↓ AGENT ONLINE         ↓ AGENT OFFLINE        │           │
│                                                       │           │
│  3a) Agent Found              3b) No Agent Found      │           │
│                                                       │           │
│      ↓ STREAM via SSE         ↓ QUEUE INCIDENT       │           │
│                               │                       │           │
│      - Open SSE connection    │ INSERT pending_       │           │
│      - Send incident          │ incidents             │           │
│      - Listen for tool calls  │ (customer_id,        │           │
│      - Relay responses        │  incident_id,        │           │
│                               │  payload)             │           │
│                                                       │           │
└──────────────────────────────────────────────────────┘           │
                         ↓                                           │
             (Agent reconnects via /api/agents/{id}/connect)        │
                         ↓                                           │
                 Deliver queued incidents                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ 4. AGENT EXECUTION (Customer VPC)                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Agent receives incident via SSE                                   │
│              ↓                                                    │
│  Agent's MCP server processes incident                            │
│              ↓                                                    │
│  Agent makes tool calls: ssh_execute, check_logs, etc.           │
│              ↓                                                    │
│  Agent streams tool responses back via SSE                        │
│              ↓                                                    │
│  Cloud orchestrator receives responses                            │
│              ↓                                                    │
│  Cloud decides: resolve | retry | escalate                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ 5. INCIDENT RESOLUTION                                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Cloud updates Incident record:                                    │
│  UPDATE incidents                                                  │
│  SET status = 'resolved', resolved_at = NOW                       │
│  WHERE id = <incident_id>                                         │
│                                                                     │
│  Cloud sends callbacks (optional):                                │
│  - Slack message: "Incident resolved"                             │
│  - PagerDuty acknowledgment                                       │
│  - Email notification                                             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Implementation Details

### Webhook Handler (`/api/webhooks/datadog`, etc.)

```python
@router.post("/datadog")
async def datadog_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
):
    # 1. Extract customer from API key
    customer = await get_customer_from_api_key(x_api_key, session)
    customer_id = customer.id if customer else None

    # 2. Create incident
    incident = Incident(
        customer_id=customer_id,
        source="datadog",
        alert_name=payload["alert_name"],
        alert_body=payload,
    )
    session.add(incident)
    await session.commit()

    # 3. Route to agent
    if customer_id:
        await route_incident_to_customer(
            customer_id,
            incident.id,
            payload,
            session,
        )
        await session.commit()

    # 4. Queue background task to run cloud orchestrator
    background_tasks.add_task(run_agent_for_incident, incident.id)

    return {"status": "accepted", "incident_id": incident.id}
```

### Route Decision (`cloud/routing.py`)

```python
async def route_incident_to_customer(
    customer_id: str,
    incident_id: str,
    incident_payload: dict,
    session: AsyncSession,
) -> dict:
    """
    Decision logic:
    1. Check if customer has online agent
    2. If agent online → return {"status": "streamed", "agent_id": "..."}
    3. If agent offline → queue incident, return {"status": "queued"}
    """
    # Find online agent (most recent heartbeat within 2 minutes)
    agent = await session.execute(
        select(Agent)
        .where(
            Agent.customer_id == customer_id,
            Agent.status == "online",
            Agent.last_heartbeat > datetime.utcnow() - timedelta(minutes=2),
        )
        .order_by(Agent.last_heartbeat.desc())
        .limit(1)
    )

    if agent:
        # Agent is online — incident will be streamed
        logger.info(f"Streaming incident to agent: {agent.id}")
        return {"status": "streamed"}
    else:
        # Agent is offline — queue incident
        pending = PendingIncident(
            customer_id=customer_id,
            incident_id=incident_id,
            incident_payload=incident_payload,
        )
        session.add(pending)
        logger.info(f"Queued incident for offline customer: {customer_id}")
        return {"status": "queued"}
```

### Agent Lifecycle

**Agent connects:**
```
POST /api/agents/{customer_id}/connect
X-API-Key: sk_abc123...
Content-Type: application/json
{ "name": "prod-agent-01" }

Response:
{
  "agent_id": "agent_xyz...",
  "customer_id": "cust_abc...",
  "status": "online"
}
```

**Agent sends heartbeats (every 30 seconds):**
```
POST /api/agents/{customer_id}/heartbeat
X-API-Key: sk_abc123...

Response:
{ "status": "ok", "agent_id": "agent_xyz...", "timestamp": "..." }
```

This updates `Agent.last_heartbeat = NOW()`.

**Agent disconnects:**
```
POST /api/agents/{customer_id}/disconnect
X-API-Key: sk_abc123...

Response:
{ "status": "ok" }
```

### Pending Incident Delivery

When agent reconnects:

```python
# On connect, cloud could deliver queued incidents:
pending_result = await session.execute(
    select(PendingIncident)
    .where(PendingIncident.customer_id == customer_id)
    .order_by(PendingIncident.created_at)
)
pending = pending_result.scalars().all()

for p in pending:
    # TODO: Phase 3.3 — stream incident via SSE
    await send_incident_to_agent(agent_id, p.incident_payload)
    # Delete after delivery
    await session.delete(p)
```

## Customer Isolation

All endpoints enforce customer isolation via API key:

**Incident Access:**
```
GET /incidents/{incident_id}
X-API-Key: sk_abc123...

# Returns incident only if:
# 1. Incident exists
# 2. Incident.customer_id matches API key's customer
# 3. Otherwise returns 403 Forbidden
```

**Incident List:**
```
GET /incidents?limit=50
X-API-Key: sk_abc123...

# Returns only incidents where:
# Incident.customer_id = <customer_id_from_api_key>
```

**Non-API-Key Access:**
```
GET /incidents/{incident_id}
# (no X-API-Key header)

# Returns only non-cloud incidents (customer_id IS NULL)
# Cloud incidents (customer_id IS NOT NULL) return 403
```

## Dashboard Access

Customers access their dashboard at:

```
https://runbookai.cloud/dashboard?api_key=sk_abc123...
```

Dashboard calls `/api/customers/{customer_id}/dashboard` with API key header:

```
GET /api/customers/cust_abc.../dashboard
X-API-Key: sk_abc123...

Response:
{
  "customer_id": "cust_abc...",
  "email": "ops@company.com",
  "incidents": [...],
  "agent": {
    "agent_id": "...",
    "name": "prod-agent-01",
    "status": "online",
    "last_heartbeat": "2026-04-28T12:05:00Z"
  },
  "stats": {
    "total_incidents": 42,
    "resolved": 38,
    "pending": 3,
    "escalated": 1,
    "avg_resolution_time_seconds": 930
  }
}
```

## Future Enhancements (Phase 3.3+)

1. **SSE Streaming** — Real-time incident delivery to agents
   - Currently: incidents queued in database
   - TODO: open SSE connection, stream incidents, handle responses

2. **Encrypted Queuing** — Encrypt pending incidents in database
   - Store `incident_payload` encrypted at rest
   - Decrypt only when delivering to agent

3. **Rate Limiting** — Per-customer incident rate limits
   - Reject incidents if customer exceeds tier limit
   - Queue incidents if approaching limit

4. **Audit Logging** — All API calls logged with customer_id
   - Who accessed which incidents
   - When agents connected/disconnected
   - Tool execution history

5. **Pricing Integration** — Meter usage
   - Count incidents per customer
   - Count tool calls per month
   - Enforce tier-based limits

## Backward Compatibility

Non-cloud incidents (no X-API-Key):
- Continue to work as before
- Not visible to cloud customers
- Not routed to any agent
- Cloud orchestrator processes them as usual

This allows gradual migration of existing customers to cloud SaaS.
