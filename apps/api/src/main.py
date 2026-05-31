from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import settings
from src.core.logging import setup_logging
from src.core.middleware import RequestIdMiddleware
from src.routers import actions_cache, analytics, audit, auth, config, github_auth, health, installations, jobs, tokens

# CORS allowed origins are a deploy-time security boundary, set via the CORS_ORIGINS env var.
_cors_origins = settings.cors_origins


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    yield


app = FastAPI(
    title="clevis API",
    version="0.1.0",
    lifespan=lifespan,
    # Interactive docs are intentionally disabled in all environments so the API
    # surface is never published. Use Postman/curl for manual endpoint testing.
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
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
app.include_router(github_auth.router, prefix="/auth/github", tags=["github-auth"])
app.include_router(installations.router, tags=["installations"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
app.include_router(actions_cache.router, prefix="/repos", tags=["actions-cache"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(tokens.router, prefix="/tokens", tags=["tokens"])
app.include_router(config.router, prefix="/config", tags=["config"])
