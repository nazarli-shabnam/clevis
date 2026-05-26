from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.app_config import get_cors_origins_at_startup, get_debug_at_startup
from src.core.logging import setup_logging
from src.core.middleware import RequestIdMiddleware
from src.routers import actions_cache, analytics, audit, auth, config, health, installations, jobs, tokens

# Read startup config from DB (entrypoint.sh already ran alembic upgrade head)
_cors_origins = get_cors_origins_at_startup()
_debug = get_debug_at_startup()


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    yield


app = FastAPI(
    title="clevis API",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url="/openapi.json" if _debug else None,
    docs_url="/docs" if _debug else None,
    redoc_url="/redoc" if _debug else None,
)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(installations.router, tags=["installations"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
app.include_router(actions_cache.router, prefix="/repos", tags=["actions-cache"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(tokens.router, prefix="/tokens", tags=["tokens"])
app.include_router(config.router, prefix="/config", tags=["config"])
