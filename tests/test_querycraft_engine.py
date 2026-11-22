import io
import sqlite3

import pandas as pd
import pytest

from querycraft_engine import (
    QueryCraftError,
    create_database,
    execute_readonly_query,
    get_schema,
    normalize_dataframe,
    read_uploaded_file,
    table_names,
)


def sample_tables():
    return {
        "orders": pd.DataFrame({
            "order_id": [1, 2, 3],
            "customer_id": [10, 11, 10],
            "amount": [100.0, 200.0, 50.0],
        }),
        "customers": pd.DataFrame({
            "customer_id": [10, 11],
            "customer_name": ["Asha", "Ravi"],
        }),
    }


def test_create_database_and_list_tables():
    conn = create_database(sample_tables())
    try:
        assert table_names(conn) == ["customers", "orders"]
        schema = get_schema(conn, "orders")
        assert "order_id" in schema["column"].tolist()
    finally:
        conn.close()


def test_select_query_returns_results_and_execution_time():
    conn = create_database(sample_tables())
    try:
        result, elapsed, truncated = execute_readonly_query(
            conn, "SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id ORDER BY total DESC"
        )
        assert result.loc[0, "customer_id"] == 11
        assert result.loc[0, "total"] == pytest.approx(200.0)
        assert elapsed >= 0
        assert truncated is False
    finally:
        conn.close()


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "DROP TABLE orders",
    "UPDATE orders SET amount = 0",
    "INSERT INTO orders VALUES (4, 10, 10)",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE ':memory:' AS extra",
])
def test_non_select_statements_are_blocked(sql):
    conn = create_database(sample_tables())
    try:
        with pytest.raises(QueryCraftError):
            execute_readonly_query(conn, sql)
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3
    finally:
        conn.close()


def test_multiple_statements_are_blocked():
    conn = create_database(sample_tables())
    try:
        with pytest.raises(QueryCraftError):
            execute_readonly_query(conn, "SELECT * FROM orders; SELECT * FROM customers;")
    finally:
        conn.close()


def test_with_select_is_allowed_but_write_cte_is_blocked():
    conn = create_database(sample_tables())
    try:
        result, _, _ = execute_readonly_query(
            conn, "WITH totals AS (SELECT SUM(amount) AS value FROM orders) SELECT value FROM totals"
        )
        assert result.loc[0, "value"] == pytest.approx(350.0)
        with pytest.raises(QueryCraftError):
            execute_readonly_query(conn, "WITH deleted AS (DELETE FROM orders RETURNING *) SELECT * FROM deleted")
    finally:
        conn.close()


def test_column_names_are_normalized_and_unique():
    result = normalize_dataframe(pd.DataFrame([[1, 2, 3]], columns=["Order ID", "Order ID", "9category"]))
    assert result.columns.tolist() == ["order_id", "order_id_2", "t_9category"]


def test_csv_upload_is_read_and_table_name_derived():
    uploaded = io.BytesIO(b"Order ID,Total Amount\n1,20\n2,40\n")
    uploaded.name = "My Orders.csv"
    uploaded.size = len(uploaded.getvalue())
    table, frame = read_uploaded_file(uploaded)
    assert table == "my_orders"
    assert frame.columns.tolist() == ["order_id", "total_amount"]
    assert len(frame) == 2


def test_results_are_truncated_to_max_rows():
    conn = create_database({"numbers": pd.DataFrame({"n": list(range(25))})})
    try:
        result, _, truncated = execute_readonly_query(conn, "SELECT * FROM numbers", max_rows=10)
        assert len(result) == 10
        assert truncated is True
    finally:
        conn.close()
