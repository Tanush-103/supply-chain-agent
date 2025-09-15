import os
import streamlit as st
import pandas as pd
from hot_order_agent_core import hoa
from hot_order_agent_core.llm import llm_parse_email
from hot_order_agent_core.promise_rate import update_orders

st.set_page_config(page_title="Hot Order Agent", layout="wide")
st.title("🔥 Hot Order Agent Dashboard (OpenAI-enabled)")

default_orders_path = "data/sample_orders.csv"
orders_df = pd.read_csv(default_orders_path)

with st.expander("📦 Current Orders (from data/sample_orders.csv)"):
    st.dataframe(orders_df, use_container_width=True)

st.subheader("Upload Orders CSV (optional)")
uploaded = st.file_uploader(
    "CSV: order_id,product,qty,customer,priority,origin,destination[,customer_email]",
    type=["csv"]
)
if uploaded is not None:
    try:
        orders_df = pd.read_csv(uploaded)
        st.success("Uploaded orders loaded.")
    except Exception as e:
        st.error(f"Failed to read uploaded CSV: {e}")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("▶️ Process Orders"):
        results = hoa.process_orders(orders_df)
        st.session_state["hoa_results"] = results
        st.success("Orders processed & customer updates sent (if SMTP configured).")

with col2:
    if st.button("🔁 Re-run on sample data"):
        orders_df = pd.read_csv(default_orders_path)
        results = hoa.process_orders(orders_df)
        st.session_state["hoa_results"] = results
        st.success("Re-run complete.")

with col3:
    if st.button("▶️ Run ATP Check"):
        orders_list = orders_df['order_id'].to_list()
        results = hoa.process_orders(orders_df)
        results_atp = update_orders(orders_list)
        results_atp = results.merge(results_atp,how="left")
        st.session_state["hoa_results"] = results_atp
        st.success("ATP Check is done & updates sent (if SMTP configured).")


st.markdown("---")
st.subheader("🧠 Try NL Parsing (no email needed)")
sample_email = st.text_area(
    "Paste a customer email (free text).",
    value="Hi team, order #7001 — can you expedite to 1 day? Also change qty 120 and ship to Boston.",
    height=160
)
if st.button("Parse with OpenAI"):
    parsed = llm_parse_email(sample_email)
    st.json(parsed)

st.markdown("---")
st.subheader("Results")
results_df = st.session_state.get("hoa_results")
if results_df is not None and not results_df.empty:
    st.dataframe(results_df, use_container_width=True)
    k1, k2, k3 = st.columns(3)
    k1.metric("Total Orders", len(results_df))
    k2.metric("At-Risk Orders", int((results_df["status"] == "At-Risk").sum()))
    k3.metric("Avg Expedite $", round(results_df["expedite_cost"].mean(), 2))

st.markdown("---")
st.subheader("Customer Communication Log")
log_path = "logs/communication.log"
os.makedirs("logs", exist_ok=True)
open(log_path, "a").close()
with open(log_path, "r") as f:
    st.text(f.read())
