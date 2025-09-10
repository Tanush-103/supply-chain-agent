# app/streamlit_app.py
import re
import streamlit as st
import pandas as pd
import plotly.express as px

from orchestrator.orchestrator import Orchestrator
from orchestrator.intent import classify_intent, Intent

st.set_page_config(page_title="Supply Chain Orchestrator", layout="wide")

# --- Bootstrap orchestrator once ---
if "orc" not in st.session_state:
    st.session_state.orc = Orchestrator()
if "messages" not in st.session_state:
    st.session_state.messages = []  # simple chat transcript
if "artifacts" not in st.session_state:
    st.session_state.artifacts = {}

orc = st.session_state.orc

# --- Sidebar: actions / quick intents ---
st.sidebar.header("Actions")
with st.sidebar:
    st.markdown("**Quick actions**")
    do_retrieve = st.button("Retrieve fast-moving items")
    do_optimize = st.button("Optimize inventory")
    do_visualize = st.button("Visualize results")
    st.markdown("---")
    st.markdown("**What-If scenario**")
    demand_pct = st.text_input("Demand change (%)", value="+15")
    cap_pct = st.text_input("Capacity change (%)", value="-10")
    do_whatif = st.button("Run What-If")

# --- Header ---
st.title("Conversational Orchestration — Supply Chain")
st.caption("Ask in natural language, or use the sidebar actions. Results render inline.")

# --- Chat input ---
prompt = st.text_input("Type a request (e.g., 'show data on fast-moving items' or 'what-if demand +15% and capacity -10%')", "")

def handle_request(text: str):
    if not text.strip():
        return
    st.session_state.messages.append({"role": "user", "content": text})
    resp = orc.handle(text)
    # log messages
    for m in resp.messages:
        st.session_state.messages.append({"role": m.role, "content": m.content})
    # stash artifacts for tabs below
    st.session_state.artifacts = resp.artifacts

# --- Wire sidebar buttons to intents ---
if do_retrieve:
    handle_request("show data on fast-moving items")
if do_optimize:
    handle_request("optimize inventory with supplier lead time rules")
if do_visualize:
    handle_request("visualize results")
if do_whatif:
    def norm_pct(s):
        s = s.strip().replace("%", "")
        return f"{'+' if s[0] not in '+-' else ''}{s}%"
    handle_request(f"what-if demand {norm_pct(demand_pct)} and capacity {norm_pct(cap_pct)}")

# --- Handle freeform prompt ---
if prompt:
    handle_request(prompt)

# --- Transcript ---
with st.expander("Conversation", expanded=False):
    for msg in st.session_state.messages:
        who = "🧑‍💼" if msg["role"] == "user" else "🤖"
        st.markdown(f"{who} **{msg['role']}**: {msg['content']}")

# --- Results / Data / Visuals ---
tabs = st.tabs(["Data", "Optimization Results", "Visualizations", "Artifacts"])

with tabs[0]:
    st.subheader("Retrieved Data")
    arts = st.session_state.artifacts or {}
    for key in ["inventory", "forecast", "suppliers", "transport_costs", "merged", "fast_moving"]:
        df = arts.get(key)
        if isinstance(df, list):
            # came through as preview dicts -> convert to DataFrame
            try:
                df = pd.DataFrame(df)
            except Exception:
                df = None
        if isinstance(df, pd.DataFrame) or (isinstance(df, list) and df):
            st.markdown(f"**{key}**")
            st.dataframe(pd.DataFrame(df))
    # If nothing yet:
    if not any(k in arts for k in ["inventory", "merged", "fast_moving"]):
        st.info("No data retrieved yet. Use the sidebar or ask for data (e.g., 'show data on fast-moving items').")

with tabs[1]:
    st.subheader("Optimization Results")
    arts = st.session_state.artifacts or {}
    results_preview = arts.get("results_preview")
    if results_preview:
        df = pd.DataFrame(results_preview)
        st.dataframe(df)
        # If you want the full results, re-run from orchestrator state:
        full = orc.state.get("last_results")
        if isinstance(full, pd.DataFrame):
            st.download_button(
                label="Download full results (CSV)",
                data=full.to_csv(index=False).encode("utf-8"),
                file_name="optimization_results.csv",
                mime="text/csv",
            )
    else:
        st.info("No optimization results yet. Click **Optimize inventory** in the sidebar.")

    summary = arts.get("summary")
    if summary:
        st.markdown("### Summary")
        c1, c2, c3 = st.columns(3)
        c1.metric("Objective", f"{summary['objective']:.2f}")
        c2.metric("Capacity Used", f"{summary['capacity_used']:.0f}")
        c3.metric("Capacity Limit", f"{summary['capacity_limit']:.0f}")

with tabs[2]:
    st.subheader("Visualizations")
    # Build visuals from orchestrator state (no file I/O needed)
    results = orc.state.get("last_results")
    if isinstance(results, pd.DataFrame) and not results.empty:
        # Orders bar (top 30)
        top = results.sort_values("order_qty", ascending=False).head(30)
        fig1 = px.bar(
            top, x="sku", y="order_qty",
            hover_data=["description", "stock_on_hand", "demand_mean", "safety_stock"],
            title="Recommended Orders (Top 30)"
        )
        st.plotly_chart(fig1, use_container_width=True)

        # Coverage scatter
        df = results.copy()
        df["post_order_stock"] = df["stock_on_hand"] + df["order_qty"] - df["demand_mean"]
        fig2 = px.scatter(
            df, x="sku", y="post_order_stock", size="order_qty",
            color=(df["safety_shortfall"] > 0),
            hover_data=["description", "safety_stock"],
            title="Post-Order Stock vs Safety"
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No results to visualize yet. Run an optimization first.")

with tabs[3]:
    st.subheader("Raw Artifacts")
    st.write(st.session_state.artifacts or {})
