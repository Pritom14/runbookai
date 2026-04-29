# Pricing Models Implementation — TODO

**Status:** Architectural foundation in place. Pricing research & implementation deferred.

## Current Infrastructure Foundation

The Phase 3 cloud architecture provides all necessary components for implementing pricing models:

### Usage Metering
- **Incident Tracking**: Every incident has `customer_id` and `created_at` timestamp
- **Tool Call Logging**: Every `AgentAction` logs tool name, input, output, duration
- **Agent Connectivity**: `Agent.last_heartbeat` tracks connection uptime
- **Query Support**: Easy to count incidents/tool_calls per customer per billing period

Example queries for metering:

```sql
-- Incidents per customer (monthly)
SELECT customer_id, COUNT(*) as incident_count
FROM incidents
WHERE customer_id = ? AND created_at >= ? AND created_at < ?
GROUP BY customer_id;

-- Tool calls per customer (monthly)
SELECT customer_id, 
       COUNT(DISTINCT i.id) as incident_count,
       SUM(CASE WHEN aa.tool_name IN ('ssh_execute', 'check_logs') THEN 1 ELSE 0 END) as tool_calls
FROM incidents i
LEFT JOIN agent_actions aa ON i.id = aa.incident_id
WHERE i.customer_id = ? AND i.created_at >= ? AND i.created_at < ?
GROUP BY customer_id;

-- Resolution time (SLA tracking)
SELECT customer_id,
       AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 60) as avg_resolution_minutes
FROM incidents
WHERE customer_id = ? AND status = 'resolved' 
      AND created_at >= ? AND created_at < ?
GROUP BY customer_id;
```

### Customer Tiers
Proposed tiers (research ongoing):

| Tier | Incidents/Month | Tool Calls/Month | Features | Price |
|------|-----------------|-----------------|----------|-------|
| Free | 100 | 1,000 | Generic webhooks only | $0 |
| Starter | 500 | 5,000 | + Slack/PagerDuty | $99 |
| Growth | 2,000 | 20,000 | + Analytics/Reports | $499 |
| Enterprise | Unlimited | Unlimited | + SLA/Support | Custom |

**TODO**: Validate with 5-10 target customers before finalizing.

### Rate Limiting Architecture
Database tables already support per-tier rate limiting:

```python
# Example: Check if incident would exceed tier limit
async def check_rate_limit(customer_id: str, tier: str, session: AsyncSession):
    tier_limits = {
        "free": 100,
        "starter": 500,
        "growth": 2000,
        "enterprise": float("inf"),
    }
    
    limit = tier_limits[tier]
    
    # Count incidents this month
    cutoff = datetime(year=datetime.now().year, month=datetime.now().month, day=1)
    result = await session.execute(
        select(func.count(Incident.id))
        .where(
            Incident.customer_id == customer_id,
            Incident.created_at >= cutoff,
        )
    )
    count = result.scalar()
    
    if count >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

### Feature Gating
```python
# Example: Slack/PagerDuty only for paid tiers
PAID_TIERS = {"starter", "growth", "enterprise"}

async def send_slack_notification(customer_id: str, message: str):
    customer = await get_customer(customer_id)
    if customer.tier not in PAID_TIERS:
        logger.info(f"Slack disabled for {customer.tier} tier")
        return
    
    await slack.post(message)
```

## What Still Needs Research & Implementation

### 1. Market Analysis (High Priority)
- [ ] Survey 5-10 target customers on willingness to pay
- [ ] Validate tier pricing ($99, $499, etc.)
- [ ] Identify killer features for each tier
- [ ] Understand per-customer incident volume distribution
- [ ] Determine SLA expectations (uptime, resolution time)

**Action**: Schedule customer interviews by end of May 2026

### 2. Competitor Research
- [ ] PagerDuty pricing structure & features
- [ ] Incident.io pricing & positioning
- [ ] Rootly pricing & customer segments
- [ ] Opsgenie feature parity analysis

**Action**: Create competitive analysis doc

### 3. Payment Processing Integration
- [ ] Stripe payment integration
- [ ] Payment webhook handling (succeeded, failed, refund)
- [ ] Invoice generation & delivery
- [ ] Failed charge retry logic
- [ ] Billing cycle management (monthly, annual)

**Required Infrastructure**:
```python
# New table: Subscription (added to models.py)
class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id: str
    customer_id: str (FK)
    tier: str  # free, starter, growth, enterprise
    billing_cycle: str  # monthly, annual
    stripe_customer_id: str
    stripe_subscription_id: str
    renewal_date: datetime
    auto_renew: bool
    created_at: datetime
    updated_at: datetime

# New endpoints:
POST /api/billing/subscribe/{customer_id}  -- create subscription
PUT /api/billing/subscribe/{customer_id}   -- change tier
POST /api/billing/webhooks/stripe          -- handle payment events
GET /api/billing/invoices/{customer_id}    -- list invoices
```

### 4. Usage Dashboard
- [ ] Show customer their incident count vs tier limit
- [ ] Display overage warnings ("60% of monthly limit used")
- [ ] Show tool call usage trends (chart)
- [ ] Forecast: "You'll exceed limit in 5 days at current rate"

**UI Component**: Add to `cloud-dashboard.html`
```html
<div class="card">
    <h2>Usage This Month</h2>
    <div class="usage-bar">
        <div class="usage-fill" style="width: 60%;">
            <span>60 / 100 incidents</span>
        </div>
    </div>
    <p style="color: #ff9800;">⚠️ Approaching limit. Upgrade to Growth tier for 2000/month.</p>
</div>
```

### 5. Billing Automation
- [ ] Monthly cron job: query metering, calculate charges
- [ ] Generate invoices (PDF)
- [ ] Charge Stripe (batch or individual)
- [ ] Handle payment failures (retry, suspend service)
- [ ] Email invoices to customer email

**Implementation Location**: `runbookai/billing/cron.py`

```python
async def monthly_billing_cron():
    """Run on 1st of month at 00:00 UTC."""
    customers = await get_all_active_subscriptions()
    for customer in customers:
        usage = await calculate_monthly_usage(customer.id)
        charge = calculate_charge(customer.tier, usage)
        invoice = await create_invoice(customer, charge)
        await stripe.charge(customer, invoice)
        await send_email(customer.email, invoice)
```

## Implementation Priority

1. **Phase 3.5** (Next): Customer interviews & market validation
2. **Phase 3.6**: Stripe integration + billing automation
3. **Phase 3.7**: Usage dashboard + feature gating
4. **Phase 3.8**: GA launch with pricing

## Risk Mitigation

- **Free Tier** — Keep generous free tier (100 incidents/month) to attract customers
- **No Lock-in** — Customers can downgrade anytime; no long-term contracts initially
- **Transparent Pricing** — Show pricing on homepage; no surprise charges
- **Trial Period** — Consider 30-day free trial for paid tiers to reduce friction
- **Support** — Email support included; separate Slack support for Enterprise only

## Notes from Phase 3 Architecture Review

From user's earlier instructions:

> Skip pricing model implementation. We'll research and implement that later after proper market analysis.

This document marks the boundary between architectural work (Phase 3.1-3.5) and pricing work (Phase 3.6+).

The database and API are ready to support:
- Usage metering (incident counting, tool call logging)
- Customer tiers (subscription table, tier column)
- Rate limiting (per-tier limits, quotas)
- Feature gating (conditional API access by tier)
- Payment processing (Stripe integration hooks)

All components can be added without refactoring the core cloud infrastructure.

## Related Documentation

- `docs/CLOUD_ARCHITECTURE.md` — full system design
- `docs/CLOUD_ONBOARDING.md` — customer setup
- `docs/CLOUD_ROUTING_ARCHITECTURE.md` — incident routing logic
