"""FastAPI entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ask, auth, dashboard, health, hsn, invoices, settings
from app.config import get_settings

# Our own messages (reads finished, retries, recovered jobs) at INFO, so they show up
# in the host's logs; libraries stay at WARNING to keep the logs readable.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the job worker with the app (it also recovers reads a restart cut off)."""
    from app.services.jobs import worker

    if get_settings().worker_enabled:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="InvoiceIQ API", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(invoices.router, prefix=API_PREFIX)
app.include_router(hsn.router, prefix=API_PREFIX)
app.include_router(ask.router, prefix=API_PREFIX)
app.include_router(dashboard.router, prefix=API_PREFIX)
app.include_router(settings.router, prefix=API_PREFIX)
