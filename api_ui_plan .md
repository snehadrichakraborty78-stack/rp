# API & UI Implementation Plan

This plan outlines the architecture for exposing the Batch Orchestrator via a thin FastAPI backend and a Streamlit frontend, explicitly handling asynchronous Human-in-the-Loop (HITL) resolution.

## Proposed Changes

### 1. Batch Completion Detection (Pipeline Update)
Before building the API, the orchestrator pipeline needs a slight adjustment for HITL boundaries:
- **`run_batch_pipeline` (Non-Blocking)**: It will spawn clusters and wait for them to hit an outcome or an interrupt. If any cluster outcomes result in an interrupt (i.e., `hitl_status=PENDING`), the pipeline will mark the `ReconciliationRun` status as `PARTIAL` and exit immediately. It will **not** run `BatchSafetyChecks` or `ReportMatchRateThroughput`.
  - **Note on `EVAL_MODE=true`**: In this mode, `interrupt()` is bypassed and everything routes to `ESCALATED_UNRESOLVED`. The run will proceed straight to `COMPLETED` in a single pass. `PARTIAL` should never occur.
- **`HitlStatus` Enum**: Ensure `PARTIAL` is supported in `RunStatus`, and `hitl_status` handles `PENDING`.

### 2. FastAPI Backend (`app/api/main.py` & `app/api/routes.py`)
We will create a lightweight FastAPI application to manage the orchestrator lifecycle and human-in-the-loop (HITL) resolution.

**Endpoints:**
- `POST /batches/run`: 
  - Accepts CSV uploads (Orders, Settlements, BankTxns).
  - Spawns `run_batch_pipeline` as a FastAPI `BackgroundTasks`.
  - Returns a new `run_id`.
- `GET /batches/{run_id}/status`:
  - Queries `ReconciliationRun` to return current `status` (IN_PROGRESS, PARTIAL, COMPLETED, FAILED).
  - Distinguishes that `PARTIAL` (some clusters awaiting human review) is a normal, expected interactive state.
- `GET /batches/{run_id}/report`:
  - Returns headline metrics. If `PARTIAL`, these metrics may be incomplete, which the UI handles.
- `GET /batches/{run_id}/pending-reviews`:
  - Queries the LangGraph Checkpointer (or `MatchGroup` table with `hitl_status=PENDING`) for clusters waiting for review.
  - Returns `cited_evidence`, `reasoning_trace`, `verification_feedback`.
- `POST /batches/{run_id}/reviews/{cluster_id}/resume`:
  - Accepts a decision (`approved` or `rejected`).
  - Calls LangGraph `Command(resume=...)` to resume the specific thread.
  - Persists the final outcome via `PersistMatchOrException`.
  - **Completion Check**: Checks if any other match groups for this `run_id` still have `hitl_status=PENDING` (or if any graph threads are still pending). This check must happen *after* the `PersistMatchOrException` commit.
  - **Concurrency Guard**: To prevent redundant safety checks on concurrent resumes, we will use an atomic conditional update (`UPDATE reconciliation_runs SET status = 'completed' WHERE id = :run_id AND status = 'partial' RETURNING id`). Only the caller that successfully flips the status executes `BatchSafetyChecks`.
  - If none remain, triggers `BatchSafetyChecks` and `finalize_run_report` (inline or background task) and updates `ReconciliationRun.status` to `COMPLETED`.
- `GET /batches/{run_id}/exceptions`:
  - Aggregates and returns exceptions from `ExceptionStaging` for the breakdown chart.

### 3. Streamlit Frontend (`app/ui/app.py`)
A single-page Streamlit application connecting to the FastAPI backend.

**Components:**
1. **Control Panel**:
   - File uploaders for the source CSVs.
   - A toggle for `EVAL_MODE`.
   - A "Trigger Run" button.
2. **Dashboard**:
   - Headline metric cards (Total Records, Match Rate, Exceptions).
   - **Safety Banner**: A prominent red banner displaying the discrepancy if `ledger_leak_variance_paise != 0`. (Only valid when status is `COMPLETED`).
   - **Exception Chart**: A bar chart or pie chart of exception categories.
3. **Review Queue (HITL)**:
   - Fetches pending reviews (visible when status is `PARTIAL`).
   - For each pending cluster, displays a drill-down table with `reasoning_trace` and `cited_evidence`.
   - "Approve" and "Reject" buttons that trigger the `/resume` endpoint.

## Open Questions
> [!WARNING]
> None at this moment. The asynchronous boundary logic for HITL is clear.

## Verification Plan
1. **API**: Start the FastAPI server and use Swagger UI (`/docs`) to verify endpoint contracts.
2. **UI**: Start Streamlit and verify the layout, file upload, and rendering of the red banner condition.
3. **Integration**: Run a batch in non-EVAL_MODE, verify it reaches `PARTIAL`, review the pending cluster, hit `/resume`, and verify it transitions to `COMPLETED` and fires `BatchSafetyChecks`.
