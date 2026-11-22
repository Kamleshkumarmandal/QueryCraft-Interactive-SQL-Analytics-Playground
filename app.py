"""QueryCraft: interactive read-only SQL analytics playground."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from querycraft_engine import (
    DEFAULT_QUERY,
    QueryCraftError,
    create_database,
    execute_readonly_query,
    get_schema,
    get_table_preview,
    read_uploaded_file,
    table_names,
)


ROOT = Path(__file__).resolve().parent
EXAMPLE_QUERIES = {
    "Sales by category": DEFAULT_QUERY,
    "Top customers by spend": """SELECT
    c.customer_name,
    c.city,
    SUM(o.quantity * p.unit_price) AS total_spend
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.product_id = o.product_id
GROUP BY c.customer_id, c.customer_name, c.city
ORDER BY total_spend DESC
LIMIT 10;""",
    "Monthly order totals": """SELECT
    substr(order_date, 1, 7) AS month,
    COUNT(*) AS orders,
    SUM(quantity) AS units
FROM orders
GROUP BY month
ORDER BY month;""",
    "Orders by status": """SELECT status, COUNT(*) AS order_count
FROM orders
GROUP BY status
ORDER BY order_count DESC;""",
}

st.set_page_config(page_title="QueryCraft | SQL Playground", page_icon="🧪", layout="wide")
st.title("🧪 QueryCraft")
st.caption("Interactive SQL analytics playground · upload, query, visualize and export")

with st.sidebar:
    st.header("Data workspace")
    uploads = st.file_uploader(
        "Upload CSV / Excel files",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
        help="Each uploaded file becomes a table named from its filename. Excel uses its first worksheet.",
    )
    use_sample = st.toggle("Include sample retail tables", value=not bool(uploads))
    st.caption("Read-only SQL · in-memory SQLite · no persistent database file")
    st.divider()
    st.subheader("Saved queries")
    if "saved_queries" not in st.session_state:
        st.session_state["saved_queries"] = {}
    saved_names = sorted(st.session_state["saved_queries"])
    selected_saved = st.selectbox("Choose a saved query", ["— Select —"] + saved_names)
    if st.button("Load saved query", disabled=selected_saved == "— Select —", use_container_width=True):
        st.session_state["sql_editor"] = st.session_state["saved_queries"][selected_saved]
        st.rerun()
    query_to_save = st.text_input("Query name", placeholder="e.g. Monthly revenue")
    if st.button("Save current query", use_container_width=True):
        current_sql = st.session_state.get("sql_editor", DEFAULT_QUERY).strip()
        if not query_to_save.strip():
            st.warning("Enter a name for this query.")
        elif not current_sql:
            st.warning("Enter SQL before saving.")
        else:
            st.session_state["saved_queries"][query_to_save.strip()] = current_sql
            st.success(f"Saved “{query_to_save.strip()}” for this session.")

tables: dict[str, pd.DataFrame] = {}
load_errors = []
if use_sample or not uploads:
    for filename in ["customers.csv", "products.csv", "orders.csv"]:
        try:
            tables[Path(filename).stem] = pd.read_csv(ROOT / "sample_data" / filename)
        except Exception as exc:
            load_errors.append(f"Sample table {filename}: {exc}")

if uploads:
    for uploaded in uploads:
        try:
            table_name, frame = read_uploaded_file(uploaded)
            tables[table_name] = frame
        except QueryCraftError as exc:
            load_errors.append(str(exc))

for error in load_errors:
    st.error(error)

try:
    connection = create_database(tables)
except Exception as exc:
    st.error(f"Could not create the in-memory database: {exc}")
    st.stop()

names = table_names(connection)
total_rows = sum(len(get_table_preview(connection, name, limit=100000)) for name in names)
m1, m2, m3 = st.columns(3)
m1.metric("Tables loaded", len(names))
m2.metric("Rows loaded", f"{total_rows:,}")
m3.metric("SQL mode", "Read-only")

query_tab, explorer_tab, chart_tab = st.tabs(["SQL Editor", "Database Explorer", "Visualize Results"])

with query_tab:
    left, right = st.columns([3, 1])
    with left:
        example_name = st.selectbox("Load an example query", ["— Choose an example —"] + list(EXAMPLE_QUERIES))
        if st.button("Load example", disabled=example_name == "— Choose an example —"):
            st.session_state["sql_editor"] = EXAMPLE_QUERIES[example_name]
            st.rerun()
    with right:
        st.markdown("**Allowed statements**")
        st.code("SELECT ...\nWITH ... SELECT ...", language="sql")

    if "sql_editor" not in st.session_state:
        st.session_state["sql_editor"] = DEFAULT_QUERY
    sql_text = st.text_area(
        "SQL query",
        key="sql_editor",
        height=260,
        help="Only one SELECT or WITH query is permitted. Database writes and schema changes are blocked.",
    )
    run_clicked = st.button("▶ Run SQL query", type="primary", use_container_width=False)

    if run_clicked:
        try:
            with st.spinner("Running read-only query..."):
                result, elapsed, truncated = execute_readonly_query(connection, sql_text)
            st.session_state["last_result"] = result
            st.session_state["last_elapsed"] = elapsed
            st.session_state["last_truncated"] = truncated
            st.session_state["last_sql"] = sql_text
            st.session_state.pop("last_query_error", None)
        except QueryCraftError as exc:
            st.session_state["last_query_error"] = str(exc)

    if st.session_state.get("last_query_error"):
        st.error(st.session_state["last_query_error"])
    if "last_result" in st.session_state:
        result = st.session_state["last_result"]
        elapsed = st.session_state["last_elapsed"]
        truncated = st.session_state["last_truncated"]
        r1, r2, r3 = st.columns(3)
        r1.metric("Rows returned", f"{len(result):,}" + ("+" if truncated else ""))
        r2.metric("Columns", len(result.columns))
        r3.metric("Execution time", f"{elapsed * 1000:.1f} ms")
        if truncated:
            st.warning("Results are capped at 5,000 rows. Add LIMIT to your query to narrow the output.")
        st.subheader("Query results")
        st.dataframe(result, use_container_width=True, hide_index=True)
        st.download_button(
            "Download results as CSV",
            data=result.to_csv(index=False).encode("utf-8"),
            file_name="querycraft_results.csv",
            mime="text/csv",
            key="download_query_results",
        )

with explorer_tab:
    st.subheader("Tables and schemas")
    selected_table = st.selectbox("Select a table", names)
    if selected_table:
        schema, preview = st.columns([1, 1.4])
        with schema:
            st.markdown(f"**Schema: `{selected_table}`**")
            st.dataframe(get_schema(connection, selected_table), use_container_width=True, hide_index=True)
        with preview:
            st.markdown(f"**Preview: `{selected_table}`**")
            st.dataframe(get_table_preview(connection, selected_table), use_container_width=True, hide_index=True)
    st.markdown("**Tables you can query**")
    st.code("\n".join(names), language="text")

with chart_tab:
    st.subheader("Visualize the latest query result")
    if "last_result" not in st.session_state or st.session_state["last_result"].empty:
        st.info("Run a query that returns rows in the SQL Editor tab first.")
    else:
        chart_data = st.session_state["last_result"]
        all_columns = chart_data.columns.tolist()
        numeric_columns = chart_data.select_dtypes(include="number").columns.tolist()
        if len(all_columns) < 1 or not numeric_columns:
            st.warning("The query result needs at least one numeric column to create a chart.")
        else:
            v1, v2, v3 = st.columns(3)
            chart_type = v1.selectbox("Chart type", ["Bar", "Line", "Scatter", "Pie"])
            x_column = v2.selectbox("Category / X axis", all_columns)
            y_column = v3.selectbox("Value / Y axis", numeric_columns)
            try:
                if chart_type == "Bar":
                    fig = px.bar(chart_data, x=x_column, y=y_column)
                elif chart_type == "Line":
                    fig = px.line(chart_data, x=x_column, y=y_column, markers=True)
                elif chart_type == "Scatter":
                    fig = px.scatter(chart_data, x=x_column, y=y_column)
                else:
                    fig = px.pie(chart_data, names=x_column, values=y_column, hole=0.35)
                st.plotly_chart(fig, use_container_width=True)
            except Exception as exc:
                st.error(f"Could not draw this chart: {exc}")

st.divider()
st.caption("QueryCraft runs a temporary in-memory SQLite database. Query results and saved SQL are held only in the current Streamlit session.")
connection.close()
