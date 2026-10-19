"""FastAPI entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ask, auth, dashboard, health, hsn, invoices, settings
from app.config import get_settings

app = FastAPI(title="InvoiceIQ API", version="1.0.0")

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
