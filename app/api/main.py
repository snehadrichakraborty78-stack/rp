"""
FastAPI application — thin HTTP layer over the Batch Orchestrator.

Exposes endpoints for:
  • Triggering a batch reconciliation run
  • Polling run status
  • Fetching headline reports and exception breakdowns
  • HITL review queue and resume
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Finance Controller API",
        description=(
            "Razorpay Track 4 AI Finance Controller — "
            "Batch reconciliation orchestrator with Human-in-the-Loop review."
        ),
        version="0.1.0",
    )

    # Allow Streamlit (or any local dev tool) to hit the API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()
