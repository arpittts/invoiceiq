"""The SQL guard against hostile and accidental queries (no LLM involved)."""

import pytest

from app.services.sql_guard import UnsafeSQL, check_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT vendor_name, SUM(total) FROM v_invoices GROUP BY vendor_name ORDER BY 2 DESC",
        "SELECT COUNT(*) FROM v_line_items WHERE gst_rate = 18",
        "WITH q AS (SELECT * FROM v_invoices WHERE invoice_date >= DATE '2026-07-01') "
        "SELECT ROUND(AVG(total), 2) FROM q",
        "SELECT DATE_TRUNC('month', invoice_date) AS m, SUM(total_gst) FROM v_invoices GROUP BY 1",
        "SELECT EXTRACT(YEAR FROM invoice_date), COALESCE(SUM(igst), 0) FROM v_invoices GROUP BY 1",
        "SELECT i.vendor_name, l.description FROM v_invoices i "
        "JOIN v_line_items l ON l.invoice_id = i.invoice_id",
        "SELECT vendor_name FROM v_invoices "
        "WHERE invoice_date >= CURRENT_DATE - INTERVAL '90 days'",
        "SELECT CASE WHEN igst > 0 THEN 'inter' ELSE 'intra' END AS kind, COUNT(*) "
        "FROM v_invoices GROUP BY 1",
        # Logic operators (sqlglot parses AND / OR as functions: this once broke dates)
        "SELECT SUM(total) FROM v_invoices "
        "WHERE invoice_date >= DATE '2026-07-01' AND invoice_date < DATE '2026-10-01'",
        "SELECT COUNT(*) FROM v_invoices WHERE (igst > 0 OR cgst > 0) AND NOT total IS NULL",
        "SELECT COUNT(*) FROM v_invoices WHERE total BETWEEN 1000 AND 5000",
        "SELECT description FROM v_line_items WHERE gst_rate IN (5, 18) AND description LIKE '%a%'",
    ],
)
def test_normal_questions_pass(sql):
    checked = check_sql(sql)
    assert "LIMIT" in checked.upper()


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DROP TABLE users", "Only SELECT"),
        ("SELECT * FROM v_invoices; DROP TABLE users", "single statement"),
        ("DELETE FROM v_invoices", "Only SELECT"),
        ("UPDATE invoices SET total = 0", "Only SELECT"),
        ("INSERT INTO users (email) VALUES ('x')", "Only SELECT"),
        ("SELECT * INTO stolen FROM v_invoices", "not allowed"),
        ("SELECT * FROM users", "not 'users'"),
        ("SELECT * FROM invoices", "not 'invoices'"),
        ("SELECT * FROM public.v_invoices", "Schema-qualified"),
        ("SELECT * FROM pg_catalog.pg_roles", "Schema-qualified"),
        ("SELECT * FROM pg_user", "not 'pg_user'"),
        ("SELECT set_config('app.user_id', '2', true)", "set_config"),
        (
            "SELECT * FROM v_invoices WHERE set_config('app.user_id', '2', true) IS NOT NULL",
            "set_config",
        ),
        ("SELECT current_setting('app.user_id')", "current_setting"),
        ("SELECT pg_sleep(30)", "pg_sleep"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
        ("SELECT * FROM v_invoices WHERE vendor_name IN (SELECT email FROM users)", "not 'users'"),
        ("WITH x AS (DELETE FROM invoices RETURNING *) SELECT * FROM x", "not allowed"),
        ("SET ROLE postgres", "Only SELECT"),
        ("RESET ROLE", "Only SELECT"),
        ("COPY v_invoices TO '/tmp/x'", "Only SELECT"),
        ("this is not sql", "parsed|SELECT"),
    ],
)
def test_hostile_queries_are_rejected(sql, reason):
    with pytest.raises(UnsafeSQL, match=reason):
        check_sql(sql)


def test_row_limit_is_added_or_capped():
    assert check_sql("SELECT * FROM v_invoices").upper().endswith("LIMIT 200")
    assert check_sql("SELECT * FROM v_invoices LIMIT 5").upper().endswith("LIMIT 5")
    assert check_sql("SELECT * FROM v_invoices LIMIT 100000").upper().endswith("LIMIT 200")
    union = check_sql(
        "SELECT vendor_name FROM v_invoices UNION SELECT description FROM v_line_items"
    )
    assert union.upper().endswith("LIMIT 200")
