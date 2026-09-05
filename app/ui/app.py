"""
Finance Controller — Streamlit Dashboard.

Single-page Streamlit application connecting to the FastAPI backend.

Components (per api_ui_plan.md §3):
  1. Control Panel  — File uploaders, EVAL_MODE toggle, Trigger Run button
  2. Dashboard      — Headline metrics, Safety Banner, Exception Chart
  3. Review Queue   — HITL pending reviews with Approve/Reject actions
"""
from __future__ import annotations

import time

import requests
import streamlit as st
import pandas as pd

# ═══════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════

API_BASE = "http://localhost:8000/batches"

st.set_page_config(
    page_title="AI Finance Controller",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ═══════════════════════════════════════════════════════════
#  CUSTOM STYLING
# ═══════════════════════════════════════════════════════════

st.markdown("""
<style>
    /* ── Global ───────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    .stApp {
        font-family: 'Inter', sans-serif;
    }

    /* ── Metric cards ─────────────────────────────────── */
    .metric-card {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 1.5rem;
        text-align: center;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
    }
    .metric-label {
        color: #8b95a5;
        font-size: 0.82rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.35rem;
    }
    .metric-value {
        font-size: 2rem;
        font-weight: 700;
        color: #e0e6ed;
    }
    .metric-value.green { color: #00e676; }
    .metric-value.amber { color: #ffc107; }
    .metric-value.red   { color: #ff5252; }
    .metric-value.blue  { color: #448aff; }

    /* ── Safety banner ────────────────────────────────── */
    .safety-banner {
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        margin: 1rem 0;
        font-weight: 600;
        font-size: 1.05rem;
        display: flex;
        align-items: center;
        gap: 0.7rem;
    }
    .safety-pass {
        background: linear-gradient(135deg, #1b5e20 0%, #2e7d32 100%);
        color: #c8e6c9;
        border: 1px solid #4caf50;
    }
    .safety-fail {
        background: linear-gradient(135deg, #b71c1c 0%, #c62828 100%);
        color: #ffcdd2;
        border: 1px solid #ef5350;
        animation: pulse-red 2s infinite;
    }
    @keyframes pulse-red {
        0%, 100% { box-shadow: 0 0 0 0 rgba(244,67,54,0.4); }
        50% { box-shadow: 0 0 20px 4px rgba(244,67,54,0.2); }
    }

    /* ── Status badges ────────────────────────────────── */
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 100px;
        font-size: 0.78rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .status-completed { background: #1b5e20; color: #a5d6a7; }
    .status-partial   { background: #e65100; color: #ffcc80; }
    .status-in_progress { background: #1565c0; color: #90caf9; }
    .status-failed    { background: #b71c1c; color: #ef9a9a; }
    .status-pending   { background: #37474f; color: #b0bec5; }

    /* ── Review cards ─────────────────────────────────── */
    .review-card {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 14px;
        padding: 1.5rem;
        margin-bottom: 1rem;
    }
    .review-header {
        font-weight: 600;
        color: #e0e6ed;
        font-size: 1rem;
        margin-bottom: 0.5rem;
    }
    .review-detail {
        color: #8b95a5;
        font-size: 0.88rem;
        line-height: 1.6;
    }

    /* ── Header ───────────────────────────────────────── */
    .app-header {
        text-align: center;
        padding: 1.5rem 0 0.5rem 0;
    }
    .app-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #448aff, #00e676);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.3rem;
    }
    .app-subtitle {
        color: #8b95a5;
        font-size: 0.95rem;
        font-weight: 400;
    }

    /* ── Section headers ──────────────────────────────── */
    .section-header {
        font-size: 1.3rem;
        font-weight: 700;
        color: #e0e6ed;
        margin: 1.5rem 0 0.8rem 0;
        padding-bottom: 0.4rem;
        border-bottom: 2px solid rgba(68, 138, 255, 0.3);
    }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
#  HEADER
# ═══════════════════════════════════════════════════════════

st.markdown("""
<div class="app-header">
    <div class="app-title">🏦 AI Finance Controller</div>
    <div class="app-subtitle">
        Razorpay Track 4 — Batch Reconciliation with Human-in-the-Loop Review
    </div>
</div>
""", unsafe_allow_html=True)

st.divider()


# ═══════════════════════════════════════════════════════════
#  SIDEBAR — CONTROL PANEL
# ═══════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### ⚙️ Control Panel")
    st.caption("Upload source CSVs and trigger a reconciliation run.")

    orders_file = st.file_uploader(
        "📋 Orders CSV", type=["csv"], key="orders_csv",
        help="Merchant orders / payment captures",
    )
    settlements_file = st.file_uploader(
        "💳 Settlements CSV", type=["csv"], key="settlements_csv",
        help="Gateway settlement advice (Razorpay payout batch)",
    )
    bank_txns_file = st.file_uploader(
        "🏦 Bank Transactions CSV", type=["csv"], key="bank_txns_csv",
        help="Bank statement line items",
    )

    with st.expander("📎 Optional CSVs", expanded=False):
        settlement_items_file = st.file_uploader(
            "📑 Settlement Items CSV", type=["csv"], key="settlement_items_csv",
            help="Line-item breakdown for settlements (enables M:N matching via ExactIdJoin)",
        )
        refunds_file = st.file_uploader(
            "💸 Refunds CSV", type=["csv"], key="refunds_csv",
            help="Refund records (needed for conservation checks and partial_refund scenarios)",
        )
        disputes_file = st.file_uploader(
            "⚖️ Disputes CSV", type=["csv"], key="disputes_csv",
            help="Dispute / chargeback records (needed for conservation checks)",
        )

    st.divider()

    eval_mode = st.toggle(
        "🧪 EVAL_MODE",
        value=False,
        help=(
            "When enabled, HITL interrupt() is bypassed. "
            "Unresolvable clusters are auto-tagged as ESCALATED_UNRESOLVED."
        ),
    )

    trigger_disabled = not (orders_file and settlements_file and bank_txns_file)

    if st.button(
        "🚀 Trigger Run",
        type="primary",
        disabled=trigger_disabled,
        use_container_width=True,
    ):
        with st.spinner("Uploading CSVs and triggering pipeline..."):
            try:
                files = {
                    "orders_csv": ("orders.csv", orders_file.getvalue(), "text/csv"),
                    "settlements_csv": ("settlements.csv", settlements_file.getvalue(), "text/csv"),
                    "bank_txns_csv": ("bank_txns.csv", bank_txns_file.getvalue(), "text/csv"),
                }
                if settlement_items_file:
                    files["settlement_items_csv"] = (
                        "settlement_items.csv", settlement_items_file.getvalue(), "text/csv",
                    )
                if refunds_file:
                    files["refunds_csv"] = (
                        "refunds.csv", refunds_file.getvalue(), "text/csv",
                    )
                if disputes_file:
                    files["disputes_csv"] = (
                        "disputes.csv", disputes_file.getvalue(), "text/csv",
                    )
                data = {"eval_mode": str(eval_mode).lower()}
                resp = requests.post(f"{API_BASE}/run", files=files, data=data, timeout=30)

                if resp.status_code in (200, 202):
                    result = resp.json()
                    st.session_state["active_run_id"] = result["run_id"]
                    st.success(f"✅ Pipeline started — Run ID: `{result['run_id']}`")
                else:
                    st.error(f"❌ API error {resp.status_code}: {resp.text}")
            except requests.ConnectionError:
                st.error("❌ Cannot connect to API. Is the FastAPI server running?")
            except Exception as e:
                st.error(f"❌ Unexpected error: {e}")

    st.divider()

    # Manual run ID entry
    st.markdown("### 🔍 Inspect Existing Run")
    manual_run_id = st.text_input(
        "Run ID",
        placeholder="paste a UUID here",
        help="Enter a run_id to inspect an existing reconciliation batch.",
    )
    if manual_run_id:
        st.session_state["active_run_id"] = manual_run_id.strip()


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════


def _get(endpoint: str) -> dict | None:
    """GET a JSON response from the API; returns None on error."""
    try:
        resp = requests.get(f"{API_BASE}/{endpoint}", timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def _status_badge(status: str) -> str:
    """Render a coloured status badge."""
    css_class = f"status-{status}"
    return f'<span class="status-badge {css_class}">{status.replace("_", " ")}</span>'


def _format_paise(paise: int | None) -> str:
    """Format paise as ₹ with 2 decimal places."""
    if paise is None:
        return "—"
    rupees = paise / 100
    return f"₹{rupees:,.2f}"


# ═══════════════════════════════════════════════════════════
#  MAIN DASHBOARD
# ═══════════════════════════════════════════════════════════

run_id = st.session_state.get("active_run_id")

if not run_id:
    st.info(
        "👈 Upload CSVs and trigger a run, or paste a Run ID to inspect results.",
        icon="ℹ️",
    )
    st.stop()


# ── Fetch report data ────────────────────────────────────

report = _get(f"{run_id}/report")

if not report:
    st.warning(f"⏳ Run `{run_id}` not found or still initializing. Refresh to check again.")
    if st.button("🔄 Refresh"):
        st.rerun()
    st.stop()


# ── Status & Auto-Refresh ────────────────────────────────

status = report.get("status", "unknown")

col_status, col_refresh = st.columns([4, 1])
with col_status:
    st.markdown(
        f'**Run** `{run_id}` &nbsp; {_status_badge(status)}',
        unsafe_allow_html=True,
    )
with col_refresh:
    if st.button("🔄 Refresh", key="refresh_main"):
        st.rerun()

if status == "in_progress":
    st.info("⏳ Pipeline is still running. Auto-refreshing in 5 seconds...", icon="⏳")
    time.sleep(5)
    st.rerun()


# ── Headline Metric Cards ────────────────────────────────

st.markdown('<div class="section-header">📊 Headline Metrics</div>', unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Total Records</div>
        <div class="metric-value blue">{report.get('total_records', 0):,}</div>
    </div>
    """, unsafe_allow_html=True)

with c2:
    rate = report.get("match_rate", 0) or 0
    rate_pct = rate * 100 if rate <= 1 else rate
    color = "green" if rate_pct >= 80 else "amber" if rate_pct >= 50 else "red"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Match Rate</div>
        <div class="metric-value {color}">{rate_pct:.1f}%</div>
    </div>
    """, unsafe_allow_html=True)

with c3:
    exc_count = report.get("exceptions", 0)
    exc_color = "green" if exc_count == 0 else "amber" if exc_count <= 5 else "red"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Exceptions</div>
        <div class="metric-value {exc_color}">{exc_count}</div>
    </div>
    """, unsafe_allow_html=True)

with c4:
    pending = report.get("pending_reviews", 0)
    pend_color = "green" if pending == 0 else "amber"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Pending Reviews</div>
        <div class="metric-value {pend_color}">{pending}</div>
    </div>
    """, unsafe_allow_html=True)


# ── Safety Banner ─────────────────────────────────────────

if status == "completed":
    leak = report.get("ledger_leak_variance_paise")
    if leak is not None and leak != 0:
        st.markdown(f"""
        <div class="safety-banner safety-fail">
            🚨 LEDGER LEAK DETECTED — Discrepancy: {_format_paise(leak)}
            ({leak:+,} paise). Investigation required.
        </div>
        """, unsafe_allow_html=True)
    elif leak is not None:
        st.markdown("""
        <div class="safety-banner safety-pass">
            ✅ Global Conservation Passed — Ledger is perfectly balanced (Δ = 0 paise).
        </div>
        """, unsafe_allow_html=True)
elif status == "partial":
    st.markdown("""
    <div class="safety-banner" style="background: linear-gradient(135deg, #e65100, #f57c00); color: #fff3e0; border: 1px solid #ff9800;">
        ⏸️ PARTIAL — Some clusters require human review before safety checks can run.
    </div>
    """, unsafe_allow_html=True)


# ── Processing Info ───────────────────────────────────────

processing_ms = report.get("processing_ms")
exhausted = report.get("exhausted_models", [])

info_cols = st.columns(2)
with info_cols[0]:
    if processing_ms:
        secs = processing_ms / 1000
        st.caption(f"⏱️ Processed in **{secs:.2f}s** ({processing_ms:,} ms)")
with info_cols[1]:
    if exhausted:
        st.caption(f"⚠️ Exhausted models: {', '.join(exhausted)}")


# ═══════════════════════════════════════════════════════════
#  EXCEPTION BREAKDOWN CHART
# ═══════════════════════════════════════════════════════════

st.markdown('<div class="section-header">🔍 Exception Breakdown</div>', unsafe_allow_html=True)

exc_data = _get(f"{run_id}/exceptions")

if exc_data and exc_data.get("summary"):
    summary = exc_data["summary"]
    df_exc = pd.DataFrame(summary)

    if not df_exc.empty:
        # Horizontal bar chart
        chart_df = df_exc.set_index("category")
        st.bar_chart(chart_df, horizontal=True, color="#448aff")

        # Detailed table
        with st.expander("📋 Exception Details", expanded=False):
            details = exc_data.get("exceptions", [])
            if details:
                df_details = pd.DataFrame(details)
                # Reorder columns for readability
                col_order = [
                    c for c in [
                        "category", "severity", "entity_type", "entity_id",
                        "variance_paise", "is_overdue", "description",
                    ]
                    if c in df_details.columns
                ]
                st.dataframe(df_details[col_order], use_container_width=True, hide_index=True)
    else:
        st.success("🎉 No exceptions recorded for this run.")
else:
    st.info("No exception data available yet.")


# ═══════════════════════════════════════════════════════════
#  HITL REVIEW QUEUE
# ═══════════════════════════════════════════════════════════

if status == "partial" or (report.get("pending_reviews", 0) > 0):
    st.markdown('<div class="section-header">👤 Human Review Queue</div>', unsafe_allow_html=True)

    reviews_data = _get(f"{run_id}/pending-reviews")

    if reviews_data and reviews_data.get("pending_reviews"):
        reviews = reviews_data["pending_reviews"]

        for i, review in enumerate(reviews):
            mg_id = review["match_group_id"]

            st.markdown(f"""
            <div class="review-card">
                <div class="review-header">
                    Cluster: {mg_id[:12]}…
                    &nbsp;|&nbsp; Tier: {review.get('tier', '—')}
                    &nbsp;|&nbsp; Confidence: {review.get('confidence_score', 0):.0%}
                    &nbsp;|&nbsp; Residual: {_format_paise(review.get('residual_paise', 0))}
                </div>
                <div class="review-detail">
                    <strong>Model:</strong> {review.get('model_used', '—')}<br>
                    <strong>Original Tier Hint:</strong> {review.get('original_tier_hint', '—')}<br>
                    <strong>Reasoning:</strong> {review.get('reasoning_trace', '—')}<br>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Cited evidence
            evidence = review.get("cited_evidence")
            if evidence:
                with st.expander(f"📎 Cited Evidence for {mg_id[:12]}…", expanded=False):
                    st.json(evidence)

            # Action buttons
            btn_cols = st.columns([1, 1, 4])
            with btn_cols[0]:
                if st.button("✅ Approve", key=f"approve_{i}_{mg_id}", type="primary"):
                    try:
                        resp = requests.post(
                            f"{API_BASE}/{run_id}/reviews/{mg_id}/resume",
                            json={"decision": "approved"},
                            timeout=15,
                        )
                        if resp.status_code == 200:
                            result = resp.json()
                            st.success(f"Approved. {result.get('message', '')}")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(f"Error: {resp.text}")
                    except Exception as e:
                        st.error(f"Request failed: {e}")

            with btn_cols[1]:
                if st.button("❌ Reject", key=f"reject_{i}_{mg_id}"):
                    try:
                        resp = requests.post(
                            f"{API_BASE}/{run_id}/reviews/{mg_id}/resume",
                            json={"decision": "rejected"},
                            timeout=15,
                        )
                        if resp.status_code == 200:
                            result = resp.json()
                            st.warning(f"Rejected. {result.get('message', '')}")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(f"Error: {resp.text}")
                    except Exception as e:
                        st.error(f"Request failed: {e}")

            st.divider()
    else:
        st.success("🎉 All reviews resolved. No pending clusters.")


# ═══════════════════════════════════════════════════════════
#  FOOTER
# ═══════════════════════════════════════════════════════════

st.divider()
st.caption(
    "AI Finance Controller v0.1 — "
    "Split routing · Dual-mode HITL · M:N allocation · Leak attribution"
)
