# QueryCraft — Interactive SQL Analytics Playground

QueryCraft is a browser-based SQL playground built with Streamlit and SQLite. Upload CSV/Excel files, explore their tables, write read-only SQL queries, visualize query results, save queries for the current browser session, and export results.

## Features

🚀project links
https://querycraft-interactive-sql-analytics-playground-hmwbsp6fs4gpx9.streamlit.app/

- Upload multiple CSV or Excel files and load them as SQLite tables.
- Explore table names, columns and SQLite data types.
- SQL editor with example queries and a session-only saved-query library.
- Run **read-only SELECT / WITH queries** against the in-memory database.
- Query time and returned row count, with a 5,000-row display cap.
- Create bar, line, scatter and pie charts from query results.
- Download query results as CSV.
- Includes a small synthetic retail database with customers, products and orders.
- Automated tests and GitHub Actions CI.

## Run locally

Python 3.10 or newer is recommended.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.

## Use the sample database

The app opens with three synthetic tables:

- `customers`: customer, city and segment.
- `products`: product, category and unit price.
- `orders`: order date, customer, product, quantity and status.

Try this query:

```sql
SELECT
    p.category,
    SUM(o.quantity * p.unit_price) AS revenue,
    COUNT(DISTINCT o.order_id) AS orders
FROM orders AS o
JOIN products AS p ON p.product_id = o.product_id
GROUP BY p.category
ORDER BY revenue DESC;
```

## Upload your own files

Upload one or more CSV or Excel files. Each file becomes a table named from its filename. Excel uploads use the first worksheet. Column names are normalized to lowercase snake_case. The app enforces a 10 MB limit per file and a 50,000-row limit per uploaded table.

## SQL safety and limits

QueryCraft is intentionally a read-only analytics playground:

- Only one SQL statement beginning with `SELECT` or `WITH` is accepted.
- SQLite's authorizer blocks database writes, schema changes, attachment commands and PRAGMA statements.
- Queries run against an in-memory SQLite database. Uploaded data is not written to a persistent database file.
- A query execution limit is applied and at most 5,000 result rows are displayed/exported.
- Saved SQL queries remain in the current Streamlit session and are not stored in a user account.

Do not upload sensitive or confidential records to a public demo. On hosted Streamlit, uploads are processed by the hosting provider while the app runs. This project does not send data to an external database or model API.

## Tests

```powershell
pip install -r requirements-dev.txt
pytest -q
```

## Deploy on Streamlit Community Cloud

1. Sign in at [Streamlit Community Cloud](https://share.streamlit.io/).
2. Select this repository and branch `main`.
3. Set the main file path to `app.py`.
4. Click **Deploy**. Streamlit provides the public `*.streamlit.app` URL.

## Data and license

The included CSV files contain synthetic demonstration data only; they are not real customer or transaction records.

MIT License — see [LICENSE](LICENSE).
