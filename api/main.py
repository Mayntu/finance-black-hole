import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import analytics, dashboard, expenses, goals
from api.routers.web import router as web_router
from api.routers.webapp_auth import router as webapp_auth_router
from api.webhook import router as webhook_router
from core.database import engine
from core.redis import close_redis

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="FinanceBlackHole API",
    version="1.0.0",
    description="AI-powered personal finance tracker API",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# JSON API routers
app.include_router(webhook_router, prefix="/api")
app.include_router(webapp_auth_router)
app.include_router(dashboard.router)
app.include_router(expenses.router)
app.include_router(goals.router)
app.include_router(analytics.router)

# Web (HTML) router — last, catches /dashboard, /goals, /history, /profile, /error
app.include_router(web_router)


@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "service": "financeblackhole-api"}


@app.on_event("shutdown")
async def shutdown() -> None:
    await engine.dispose()
    await close_redis()
    logger.info("api_shutdown")
