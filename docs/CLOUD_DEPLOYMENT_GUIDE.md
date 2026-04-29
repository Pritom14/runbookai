# RunbookAI Cloud Deployment Guide

## Overview

This guide covers deploying RunbookAI as a managed cloud service for SaaS customers.

## Architecture Summary

```
┌─────────────────────────────────────┐
│   RunbookAI Cloud (AWS/GCP)         │
│                                     │
│   ├─ FastAPI Server (port 7000)     │
│   ├─ SQLite/PostgreSQL DB           │
│   ├─ LLM Provider (Anthropic API)   │
│   └─ Incident Storage               │
│                                     │
└─────────────────────────────────────┘
         ↑ Webhooks                     ↑ Heartbeats
         │ (Datadog, Grafana)          │ (Agent keep-alive)
         │                             │
    ┌────────────────┐            ┌────────────────┐
    │  Monitoring    │            │ VPC Agent 1    │
    │  Systems       │            │ (prod cluster) │
    └────────────────┘            └────────────────┘
                                  ┌────────────────┐
                                  │ VPC Agent 2    │
                                  │ (staging)      │
                                  └────────────────┘
```

## Prerequisites

- Python 3.9+
- Docker & Docker Compose (for containerized deployment)
- PostgreSQL 13+ (recommended for production, SQLite ok for testing)
- Anthropic API key (for Claude LLM)
- AWS/GCP account (for cloud hosting)

## Environment Configuration

Create `.env` file with cloud-specific settings:

```bash
# Cloud
CLOUD_ENABLED=true
CLOUD_API_BASE_URL=https://runbookai.cloud

# Database (PostgreSQL for production)
DATABASE_URL=postgresql+asyncpg://user:pass@db.example.com/runbookai

# LLM (Anthropic)
LLM_BASE_URL=https://api.anthropic.com/v1
LLM_MODEL=claude-opus-4-1
ANTHROPIC_API_KEY=sk-ant-...

# Incident Processing
SUGGEST_MODE=false  # Autonomous execution in cloud

# Optional: Integrations
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
PAGERDUTY_API_KEY=...
PAGERDUTY_WEBHOOK_SECRET=...
```

## Deployment Options

### Option 1: Docker Compose (Development/Testing)

```bash
# Using provided docker-compose.yml
cd demo/cloud
docker-compose up -d

# Verify
curl http://localhost:7000/health
# {"status": "ok"}
```

### Option 2: Kubernetes (Production)

**Create Kubernetes deployment:**

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: runbookai-cloud
spec:
  replicas: 3
  selector:
    matchLabels:
      app: runbookai-cloud
  template:
    metadata:
      labels:
        app: runbookai-cloud
    spec:
      containers:
      - name: runbookai
        image: runbookai:latest
        ports:
        - containerPort: 7000
        env:
        - name: CLOUD_ENABLED
          value: "true"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: runbookai-secrets
              key: database-url
        - name: ANTHROPIC_API_KEY
          valueFrom:
            secretKeyRef:
              name: runbookai-secrets
              key: anthropic-api-key
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 7000
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 7000
          initialDelaySeconds: 5
          periodSeconds: 5

---
apiVersion: v1
kind: Service
metadata:
  name: runbookai-cloud
spec:
  type: LoadBalancer
  ports:
  - port: 443
    targetPort: 7000
  selector:
    app: runbookai-cloud

---
apiVersion: v1
kind: Secret
metadata:
  name: runbookai-secrets
type: Opaque
stringData:
  database-url: postgresql+asyncpg://...
  anthropic-api-key: sk-ant-...
```

**Deploy:**
```bash
kubectl create namespace runbookai
kubectl apply -f k8s/deployment.yaml -n runbookai
kubectl port-forward -n runbookai svc/runbookai-cloud 7000:443
```

### Option 3: AWS Lambda + RDS

**Container image:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

# AWS Lambda expects specific handler
ENV LAMBDA_HANDLER=runbookai.main.app

CMD ["gunicorn", "--bind", "0.0.0.0:7000", "--worker-class", "uvicorn.workers.UvicornWorker", "runbookai.main:app"]
```

**Push to ECR:**
```bash
aws ecr create-repository --repository-name runbookai --region us-east-1
docker build -t runbookai:latest .
docker tag runbookai:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/runbookai:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/runbookai:latest
```

**Create Lambda function via AWS console:**
- Image: `123456789.dkr.ecr.us-east-1.amazonaws.com/runbookai:latest`
- Memory: 1024 MB
- Timeout: 300 seconds
- VPC: Same VPC as RDS
- Environment: DATABASE_URL, ANTHROPIC_API_KEY

**API Gateway:**
- Create REST API
- Create proxy resource `{proxy+}`
- Create ANY method
- Integration: Lambda function (runbookai)
- Deploy to stage `prod`

## Database Migration

### SQLite → PostgreSQL

For production deployment, migrate from SQLite to PostgreSQL:

```bash
# Install PostgreSQL driver
pip install asyncpg

# Update config
export DATABASE_URL=postgresql+asyncpg://user:pass@db.example.com/runbookai

# Run migration (tables auto-created on startup)
python -c "from runbookai.main import app; asyncio.run(init_db())"

# Verify
psql postgresql://user:pass@db.example.com/runbookai
\dt  -- list tables
SELECT COUNT(*) FROM customers;
```

### Data Backup

```bash
# Daily backup to S3
pg_dump postgresql://user:pass@db.example.com/runbookai | \
  gzip | \
  aws s3 cp - s3://runbookai-backups/db-$(date +%Y%m%d).sql.gz

# Restore from backup
aws s3 cp s3://runbookai-backups/db-20260428.sql.gz - | \
  gunzip | \
  psql postgresql://user:pass@db.example.com/runbookai
```

## SSL/TLS Configuration

### Self-Signed Certificate (Testing)
```bash
openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365
```

### Let's Encrypt (Production)
```bash
certbot certonly --standalone -d runbookai.cloud
# Certificates in /etc/letsencrypt/live/runbookai.cloud/
```

### Nginx Reverse Proxy
```nginx
server {
    listen 443 ssl http2;
    server_name runbookai.cloud;

    ssl_certificate /etc/letsencrypt/live/runbookai.cloud/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/runbookai.cloud/privkey.pem;

    location / {
        proxy_pass http://localhost:7000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (for SSE)
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
```

## Monitoring & Logging

### Prometheus Metrics
```bash
# Add prometheus-client
pip install prometheus-client

# Export metrics at /metrics
from prometheus_client import Counter, Gauge, start_http_server

incident_count = Counter('runbookai_incidents_total', 'Total incidents')
agent_online = Gauge('runbookai_agents_online', 'Agents currently online')

# In webhook handler:
incident_count.inc()
```

### CloudWatch (AWS)
```python
import logging
import watchtower

logging.basicConfig(
    handlers=[watchtower.CloudWatchLogHandler()],
    level=logging.INFO,
)
```

### Datadog (All platforms)
```python
from ddtrace import tracer

@tracer.wrap()
async def route_incident_to_customer(...):
    # Automatically traced
    ...
```

## Scaling Considerations

### Horizontal Scaling
- Cloud server is stateless
- Multiple instances behind load balancer
- Database connection pooling (pgbouncer)

### Rate Limiting
```python
from slowapi import Limiter

limiter = Limiter(key_func=get_api_key)

@router.post("/webhooks/generic")
@limiter.limit("1000/hour")  # per API key
async def generic_webhook(...):
    ...
```

### Caching
```python
from fastapi_cache2 import FastAPICache2

@cached(expire=300)  # 5 minutes
async def get_runbook(alert_name: str):
    ...
```

## Incident Response

### 503 Service Unavailable
If cloud service is down:
- Agents will fail to connect
- Incidents will queue in agents' local state
- Manual incident response available via SSH
- Service auto-recovers when cloud comes back online

### Database Corruption
- Keep hourly RDS backups
- Test recovery monthly
- Automated CloudWatch alert if backup fails

### Security Breach
- Rotate all API keys immediately: `UPDATE customers SET api_key = ...`
- Audit incident access logs
- Email affected customers
- Change Anthropic API key

## Testing Deployment

### Health Check
```bash
curl -I https://runbookai.cloud/health
# HTTP/1.1 200 OK
```

### Register Test Customer
```bash
curl -X POST https://runbookai.cloud/api/customers/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com"}'
```

### Send Test Incident
```bash
curl -X POST https://runbookai.cloud/webhooks/generic \
  -H "X-API-Key: sk_test..." \
  -H "Content-Type: application/json" \
  -d '{"alert_name": "Test Alert", "service": "test"}'
```

### Access Dashboard
Open browser: `https://runbookai.cloud/dashboard?api_key=sk_test...`

## Rollback Plan

If new version causes issues:

```bash
# Identify bad version
kubectl rollout history deployment/runbookai-cloud -n runbookai

# Rollback to previous
kubectl rollout undo deployment/runbookai-cloud -n runbookai --to-revision=N

# Verify
kubectl port-forward svc/runbookai-cloud 7000:443
curl http://localhost:7000/health
```

## Disaster Recovery

### RTO/RPO Targets
- **RTO** (Recovery Time Objective): 1 hour
- **RPO** (Recovery Point Objective): 15 minutes

### Backup Strategy
- Hourly PostgreSQL snapshots (RDS)
- Daily full backup to S3
- Incident logs exported to S3 for long-term storage
- Test restore monthly

### Failover
- Primary region: `us-east-1`
- Standby region: `us-west-2` (warm standby)
- DNS failover via Route 53 health checks
- Manual failover: update CNAME to standby

## Post-Deployment Checklist

- [ ] Cloud enabled in config
- [ ] Database migrations run
- [ ] SSL certificates valid
- [ ] Monitoring/alerting configured
- [ ] Customer onboarding docs reviewed
- [ ] First 5 customers onboarded
- [ ] Incident response tested
- [ ] Backup/restore tested
- [ ] Load testing completed (1000 req/sec)
- [ ] Security audit passed
- [ ] Legal/compliance review done

## Support & Debugging

### Agent Connection Issues
```bash
# Check agent logs
tail -f ~/.runbookai/agent.log

# Verify cloud reachability
curl -I https://runbookai.cloud/health

# Check agent status
curl https://runbookai.cloud/api/agents/{customer_id}/status \
  -H "X-API-Key: sk_..."
```

### Incident Not Being Routed
```bash
# Check cloud logs
docker logs runbookai-cloud

# Verify customer exists
psql $DATABASE_URL -c "SELECT * FROM customers WHERE api_key = 'sk_...';"

# Check incident in database
psql $DATABASE_URL -c "SELECT * FROM incidents ORDER BY created_at DESC LIMIT 1;"

# Check agent connection
psql $DATABASE_URL -c "SELECT * FROM agents WHERE customer_id = '...' ORDER BY last_heartbeat DESC;"
```

## Next Steps

- See `docs/CLOUD_ONBOARDING.md` for customer setup
- See `docs/PRICING_MODELS_TODO.md` for billing implementation
- See `docs/CLOUD_ARCHITECTURE.md` for full system design
