"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vsa_api.config import settings
from vsa_api.db.session import engine, Base
from vsa_api.routers import containers, domains, certs, audit_logs, stacks, vps, agent, traffic


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (in production, use Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="VSA Dashboard API",
    description="FlowBiz VPS Admin Suite — centralized management dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(containers.router, prefix="/api")
app.include_router(domains.router, prefix="/api")
app.include_router(certs.router, prefix="/api")
app.include_router(audit_logs.router, prefix="/api")
app.include_router(stacks.router, prefix="/api")
app.include_router(vps.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(traffic.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def run():
    import uvicorn
    uvicorn.run("vsa_api.main:app", host="0.0.0.0", port=8000, reload=True)
