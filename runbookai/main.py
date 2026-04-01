"""RunbookAI — FastAPI application entry point."""

from fastapi import FastAPI

from runbookai.api.approvals import router as approvals_router
from runbookai.api.incidents import router as incidents_router
from runbookai.api.webhooks import router as webhooks_router
from runbookai.database import init_db

app = FastAPI(
    title="RunbookAI",
    description="Autonomous incident response agent",
    version="0.1.0",
)

app.include_router(webhooks_router)
app.include_router(approvals_router)
app.include_router(incidents_router)


@app.on_event("startup")
async def startup_event() -> None:
    await init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}


def start():
    import uvicorn
    uvicorn.run("runbookai.main:app", host="0.0.0.0", port=7000, reload=True)
