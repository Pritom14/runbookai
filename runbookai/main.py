"""RunbookAI — FastAPI application entry point."""

import pathlib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from runbookai.api.agents import router as agents_router
from runbookai.api.analysis import router as analysis_router
from runbookai.api.approvals import router as approvals_router
from runbookai.api.bmc import router as bmc_router
from runbookai.api.brain import router as brain_router
from runbookai.api.customer_dashboard import router as dashboard_router
from runbookai.api.customers import router as customers_router
from runbookai.api.hosts import router as hosts_router
from runbookai.api.incidents import router as incidents_router
from runbookai.api.postmortem import router as postmortem_router
from runbookai.api.runbooks import router as runbooks_router
from runbookai.api.webhooks import router as webhooks_router
from runbookai.database import init_db
from runbookai.runbook_loader import load_runbooks_from_disk

app = FastAPI(
    title="RunbookAI",
    description="Autonomous incident response agent",
    version="0.1.0",
)

app.include_router(agents_router)
app.include_router(customers_router)
app.include_router(dashboard_router)
app.include_router(webhooks_router)
app.include_router(approvals_router)
app.include_router(bmc_router)
# analysis, postmortem, and brain must be registered before incidents so
# /incidents/analysis, /incidents/{id}/postmortem, and /brain/incidents/* are not captured
# by the /incidents/{incident_id} wildcard route.
app.include_router(analysis_router)
app.include_router(postmortem_router)
app.include_router(brain_router)
app.include_router(incidents_router)
app.include_router(runbooks_router)
app.include_router(hosts_router)
_static = str(pathlib.Path(__file__).parent / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.on_event("startup")
async def startup_event() -> None:
    await init_db()
    await load_runbooks_from_disk()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/dashboard")
async def cloud_dashboard():
    """Serve the cloud customer dashboard."""
    from fastapi.responses import FileResponse
    dashboard_file = pathlib.Path(__file__).parent / "static" / "cloud-dashboard.html"
    return FileResponse(dashboard_file, media_type="text/html")


def start():
    import uvicorn
    uvicorn.run("runbookai.main:app", host="0.0.0.0", port=7000, reload=True)
