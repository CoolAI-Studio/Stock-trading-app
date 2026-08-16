import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routers.health import router as health_router
from app.config import settings
from app.services.market_loop import run_forever


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_forever(stop_event)) if settings.WORKER_ENABLED else None

    yield

    if worker_task is not None:
        stop_event.set()
        await worker_task


app = FastAPI(title="Trading App API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(api_router, prefix="/api")
