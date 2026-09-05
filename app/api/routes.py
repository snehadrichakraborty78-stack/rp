"""
FastAPI routes for the Batch Orchestrator API.

Endpoints (per api_ui_plan.md §2):
  POST /batches/run               — Upload CSVs, trigger run_batch_pipeline
  GET  /batches/{run_id}/status    — Poll ReconciliationRun status
  GET  /batches/{run_id}/report    — Headline metrics
  GET  /batches/{run_id}/pending-reviews — HITL review queue
  POST /batches/{run_id}/reviews/{cluster_id}/resume — Resume a suspended cluster
  GET  /batches/{run_id}/exceptions — Exception breakdown
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import async_session
from app.db.enums import (
    ExceptionCategory,
    HitlStatus,
    MatchTier,
    RunStatus,
)
from app.db.models import (
    ExceptionStaging,
    MatchAllocation,
    MatchGroup,
    ReconciliationRun,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/batches", tags=["batches"])


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════
from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel
from app.agent.tools import (
    query_order,
    query_refund,
    query_dispute,
    query_settlement,
    query_bank_transaction,
    check_fee_schedule,
)

def _default_llm_factory(model_name: str) -> BaseChatModel:
    """Initialize models using OpenAI's compatible endpoint for both Groq and OpenAI."""
    if model_name.startswith("groq/"):
        groq_model = model_name.replace("groq/", "")
        return ChatOpenAI(
            model=groq_model,
            api_key=os.getenv("GROQ_API_KEY", ""),
            base_url="https://api.groq.com/openai/v1",
            temperature=0,
            max_retries=2,
        )
    
    return ChatOpenAI(
        model=model_name,
        api_key=os.getenv("OPENAI_API_KEY", ""),
        temperature=0,
        max_retries=2,
    )

DEFAULT_TOOL_MAP = {
    "query_order": query_order,
    "query_refund": query_refund,
    "query_dispute": query_dispute,
    "query_settlement": query_settlement,
    "query_bank_transaction": query_bank_transaction,
    "check_fee_schedule": check_fee_schedule,
}


def _parse_csv(content: bytes) -> list[dict[str, Any]]:
    """Parse raw CSV bytes into a list of dicts, casting integer fields."""
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        parsed = {}
        for k, v in row.items():
            if k and v and (k.endswith("_paise") or k.endswith("_points") or k.endswith("_bps")):
                try:
                    parsed[k] = int(v)
                except ValueError:
                    parsed[k] = v
            else:
                parsed[k] = v
        rows.append(parsed)
    return rows


async def _run_pipeline_background(
    run_id: uuid.UUID,
    orders: list[dict],
    settlements: list[dict],
    bank_transactions: list[dict],
    source_checksum: str,
    eval_mode: bool,
    settlement_items: list[dict] | None = None,
    refunds: list[dict] | None = None,
    disputes: list[dict] | None = None,
) -> None:
    """Background task that runs the batch pipeline end-to-end."""
    from app.orchestrator.pipeline import run_batch_pipeline

    try:
        async with async_session() as session:
            async with session.begin():
                await run_batch_pipeline(
                    session,
                    orders=orders,
                    settlements=settlements,
                    bank_transactions=bank_transactions,
                    settlement_items=settlement_items,
                    refunds=refunds,
                    disputes=disputes,
                    source_checksum=source_checksum,
                    eval_mode=eval_mode,
                    llm_factory=_default_llm_factory,
                    tool_map=DEFAULT_TOOL_MAP,
                )
    except Exception:
        logger.exception("Background pipeline failed for run %s", run_id)
        # Mark the run as FAILED
        try:
            async with async_session() as session:
                async with session.begin():
                    stmt = (
                        update(ReconciliationRun)
                        .where(ReconciliationRun.id == run_id)
                        .values(
                            status=RunStatus.FAILED,
                            run_completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.execute(stmt)
        except Exception:
            logger.exception("Failed to mark run %s as FAILED", run_id)


# ═══════════════════════════════════════════════════════════
#  POST /batches/run
# ═══════════════════════════════════════════════════════════


@router.post("/run")
async def trigger_run(
    background_tasks: BackgroundTasks,
    orders_csv: UploadFile = File(...),
    settlements_csv: UploadFile = File(...),
    bank_txns_csv: UploadFile = File(...),
    settlement_items_csv: UploadFile | None = File(None),
    refunds_csv: UploadFile | None = File(None),
    disputes_csv: UploadFile | None = File(None),
    eval_mode: bool = Form(False),
) -> JSONResponse:
    """Upload CSVs and trigger a batch reconciliation run.

    Required: orders_csv, settlements_csv, bank_txns_csv.
    Optional: settlement_items_csv, refunds_csv, disputes_csv.

    Returns the new ``run_id`` immediately; the pipeline runs in the background.
    """
    orders_bytes = await orders_csv.read()
    settlements_bytes = await settlements_csv.read()
    bank_txns_bytes = await bank_txns_csv.read()

    # Parse optional CSVs
    settlement_items_bytes = await settlement_items_csv.read() if settlement_items_csv else None
    refunds_bytes = await refunds_csv.read() if refunds_csv else None
    disputes_bytes = await disputes_csv.read() if disputes_csv else None

    # SHA-256 checksum for duplicate batch guard (include optional files)
    combined = orders_bytes + settlements_bytes + bank_txns_bytes
    if settlement_items_bytes:
        combined += settlement_items_bytes
    if refunds_bytes:
        combined += refunds_bytes
    if disputes_bytes:
        combined += disputes_bytes
    source_checksum = hashlib.sha256(combined).hexdigest()

    orders = _parse_csv(orders_bytes)
    settlements = _parse_csv(settlements_bytes)
    bank_transactions = _parse_csv(bank_txns_bytes)
    settlement_items = _parse_csv(settlement_items_bytes) if settlement_items_bytes else None
    refunds = _parse_csv(refunds_bytes) if refunds_bytes else None
    disputes = _parse_csv(disputes_bytes) if disputes_bytes else None

    run_id = uuid.uuid4()

    background_tasks.add_task(
        _run_pipeline_background,
        run_id=run_id,
        orders=orders,
        settlements=settlements,
        bank_transactions=bank_transactions,
        source_checksum=source_checksum,
        eval_mode=eval_mode,
        settlement_items=settlement_items,
        refunds=refunds,
        disputes=disputes,
    )

    return JSONResponse(
        status_code=202,
        content={
            "run_id": str(run_id),
            "status": "in_progress",
            "message": "Batch pipeline started.",
        },
    )


# ═══════════════════════════════════════════════════════════
#  GET /batches/{run_id}/status
# ═══════════════════════════════════════════════════════════


@router.get("/{run_id}/status")
async def get_status(run_id: uuid.UUID) -> JSONResponse:
    """Poll the current status of a reconciliation run."""
    async with async_session() as session:
        stmt = select(ReconciliationRun).where(ReconciliationRun.id == run_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        return JSONResponse(status_code=404, content={"error": "Run not found."})

    return JSONResponse(content={
        "run_id": str(run.id),
        "status": run.status.value,
        "eval_mode": run.eval_mode,
        "total_records": run.total_records,
        "match_rate": run.match_rate,
        "ledger_leak_variance_paise": run.ledger_leak_variance_paise,
        "processing_ms": run.processing_ms,
        "run_started_at": run.run_started_at.isoformat() if run.run_started_at else None,
        "run_completed_at": run.run_completed_at.isoformat() if run.run_completed_at else None,
    })


# ═══════════════════════════════════════════════════════════
#  GET /batches/{run_id}/report
# ═══════════════════════════════════════════════════════════


@router.get("/{run_id}/report")
async def get_report(run_id: uuid.UUID) -> JSONResponse:
    """Return headline metrics for a reconciliation run.

    If status is PARTIAL, metrics may be incomplete — the UI handles this.
    """
    async with async_session() as session:
        stmt = select(ReconciliationRun).where(ReconciliationRun.id == run_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

        if not run:
            return JSONResponse(status_code=404, content={"error": "Run not found."})

        # Count match groups
        mg_stmt = select(func.count()).select_from(MatchGroup).where(
            MatchGroup.reconciliation_run_id == run_id
        )
        matched = (await session.execute(mg_stmt)).scalar() or 0

        # Count exceptions
        exc_stmt = select(func.count()).select_from(ExceptionStaging).where(
            ExceptionStaging.reconciliation_run_id == run_id
        )
        exceptions = (await session.execute(exc_stmt)).scalar() or 0

        # Count pending HITL reviews
        pending_stmt = select(func.count()).select_from(MatchGroup).where(
            MatchGroup.reconciliation_run_id == run_id,
            MatchGroup.hitl_status == HitlStatus.PENDING,
        )
        pending_reviews = (await session.execute(pending_stmt)).scalar() or 0

    return JSONResponse(content={
        "run_id": str(run.id),
        "status": run.status.value,
        "total_records": run.total_records or 0,
        "matched_groups": matched,
        "exceptions": exceptions,
        "pending_reviews": pending_reviews,
        "match_rate": run.match_rate or 0.0,
        "ledger_leak_variance_paise": run.ledger_leak_variance_paise,
        "processing_ms": run.processing_ms,
        "exhausted_models": run.exhausted_models or [],
    })


# ═══════════════════════════════════════════════════════════
#  GET /batches/{run_id}/pending-reviews
# ═══════════════════════════════════════════════════════════


@router.get("/{run_id}/pending-reviews")
async def get_pending_reviews(run_id: uuid.UUID) -> JSONResponse:
    """Return clusters awaiting human review."""
    async with async_session() as session:
        stmt = (
            select(MatchGroup)
            .where(
                MatchGroup.reconciliation_run_id == run_id,
                MatchGroup.hitl_status == HitlStatus.PENDING,
            )
            .order_by(MatchGroup.created_at)
        )
        result = await session.execute(stmt)
        pending = result.scalars().all()

    reviews = []
    for mg in pending:
        reviews.append({
            "match_group_id": str(mg.id),
            "tier": mg.tier.value if mg.tier else None,
            "confidence_score": mg.confidence_score,
            "residual_paise": mg.residual_paise,
            "reasoning_trace": mg.reasoning_trace,
            "cited_evidence": mg.cited_evidence,
            "model_used": mg.model_used,
            "original_tier_hint": mg.original_tier_hint,
            "processing_ms": mg.processing_ms,
            "created_at": mg.created_at.isoformat() if mg.created_at else None,
        })

    return JSONResponse(content={"pending_reviews": reviews})


# ═══════════════════════════════════════════════════════════
#  POST /batches/{run_id}/reviews/{match_group_id}/resume
# ═══════════════════════════════════════════════════════════


class ReviewDecision(BaseModel):
    """Request body for resuming a pending review."""
    decision: str  # "approved" or "rejected"


@router.post("/{run_id}/reviews/{match_group_id}/resume")
async def resume_review(
    run_id: uuid.UUID,
    match_group_id: uuid.UUID,
    body: ReviewDecision,
) -> JSONResponse:
    """Resolve a pending HITL review.

    Accepts ``approved`` or ``rejected``.
    On the last resolved review, triggers BatchSafetyChecks via an atomic
    conditional update to prevent redundant safety runs.
    """
    async with async_session() as session:
        async with session.begin():
            # Fetch the match group
            stmt = select(MatchGroup).where(
                MatchGroup.id == match_group_id,
                MatchGroup.reconciliation_run_id == run_id,
            )
            result = await session.execute(stmt)
            mg = result.scalar_one_or_none()

            if not mg:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Match group not found."},
                )

            if mg.hitl_status != HitlStatus.PENDING:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Match group is not pending (status={mg.hitl_status.value})."},
                )

            # Apply the decision
            if body.decision == "approved":
                mg.hitl_status = HitlStatus.APPROVED
                mg.verified = True
            else:
                mg.hitl_status = HitlStatus.REJECTED
                mg.verified = False
                # Route to exception staging
                from app.orchestrator.persistence import persist_exception
                from app.db.enums import ExceptionSeverity
                await persist_exception(
                    session,
                    run_id=run_id,
                    category=ExceptionCategory.ESCALATED_UNRESOLVED,
                    severity=ExceptionSeverity.HIGH,
                    description=(
                        f"Human reviewer rejected match group {match_group_id}. "
                        f"Reasoning: {mg.reasoning_trace}"
                    ),
                )

            session.add(mg)
            await session.flush()

            # ── Completion Check ──────────────────────────────
            # Are there any remaining PENDING match groups for this run?
            remaining_stmt = select(func.count()).select_from(MatchGroup).where(
                MatchGroup.reconciliation_run_id == run_id,
                MatchGroup.hitl_status == HitlStatus.PENDING,
            )
            remaining = (await session.execute(remaining_stmt)).scalar() or 0

            if remaining == 0:
                # ── Atomic Conditional Update (Concurrency Guard) ──
                # Only the caller that successfully flips PARTIAL → COMPLETED
                # gets to run safety checks.
                flip_stmt = (
                    update(ReconciliationRun)
                    .where(
                        ReconciliationRun.id == run_id,
                        ReconciliationRun.status == RunStatus.PARTIAL,
                    )
                    .values(status=RunStatus.COMPLETED)
                    .returning(ReconciliationRun.id)
                )
                flip_result = await session.execute(flip_stmt)
                flipped = flip_result.scalar_one_or_none()

                if flipped:
                    # We won the race — run safety checks
                    from app.orchestrator.safety import run_batch_safety_checks
                    variance = await run_batch_safety_checks(session, run_id)

                    # Update final metrics
                    run_update = (
                        update(ReconciliationRun)
                        .where(ReconciliationRun.id == run_id)
                        .values(
                            ledger_leak_variance_paise=variance,
                            run_completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.execute(run_update)

    return JSONResponse(content={
        "match_group_id": str(match_group_id),
        "decision": body.decision,
        "remaining_pending": remaining,
        "message": (
            "All reviews completed. Safety checks executed."
            if remaining == 0
            else f"{remaining} review(s) still pending."
        ),
    })


# ═══════════════════════════════════════════════════════════
#  GET /batches/{run_id}/exceptions
# ═══════════════════════════════════════════════════════════


@router.get("/{run_id}/exceptions")
async def get_exceptions(run_id: uuid.UUID) -> JSONResponse:
    """Return exception breakdown for the chart."""
    async with async_session() as session:
        # Group by category with counts
        stmt = (
            select(
                ExceptionStaging.category,
                func.count().label("count"),
            )
            .where(ExceptionStaging.reconciliation_run_id == run_id)
            .group_by(ExceptionStaging.category)
        )
        result = await session.execute(stmt)
        rows = result.all()

        # Also fetch individual exceptions for drill-down
        detail_stmt = (
            select(ExceptionStaging)
            .where(ExceptionStaging.reconciliation_run_id == run_id)
            .order_by(ExceptionStaging.created_at)
        )
        detail_result = await session.execute(detail_stmt)
        exceptions = detail_result.scalars().all()

    summary = [
        {"category": row.category.value if hasattr(row.category, 'value') else str(row.category), "count": row.count}
        for row in rows
    ]

    details = []
    for exc in exceptions:
        details.append({
            "id": str(exc.id),
            "category": exc.category.value if hasattr(exc.category, 'value') else str(exc.category),
            "severity": exc.severity.value if hasattr(exc.severity, 'value') else str(exc.severity),
            "entity_type": exc.entity_type,
            "entity_id": exc.entity_id,
            "variance_paise": exc.variance_paise,
            "is_overdue": exc.is_overdue,
            "description": exc.description,
        })

    return JSONResponse(content={
        "summary": summary,
        "exceptions": details,
    })
