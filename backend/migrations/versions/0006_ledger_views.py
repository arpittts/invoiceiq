"""Ledger Q&A: read-only views over approved invoices and a role that can only read them.

- ledger_reader: a role without login. The API switches to it (SET LOCAL ROLE)
  inside a short transaction before running model-written SQL. It may SELECT from
  the two views and nothing else, so it can't write or see other tables.
- ledger_query_context: (connection id, user id) rows written by the server, as the
  app's own user, inside a transaction that is always rolled back, so nothing is
  ever stored. ledger_reader cannot write to it.
- v_invoices / v_line_items: approved invoices only, and only for the user that the
  context table names for the current connection (pg_backend_pid()). No row there
  means the views return nothing (safe by default).

Why not a setting like app.user_id? Any role can change a setting (set_config),
so a query that slipped past the SQL guard could switch users mid-query. A table
the read-only role can't write to keeps isolation enforced by the database itself.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

USER_FILTER = (
    "user_id = (SELECT c.user_id FROM ledger_query_context c "
    "WHERE c.backend_pid = pg_backend_pid())"
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ledger_query_context (
            backend_pid integer PRIMARY KEY,
            user_id integer NOT NULL
        )
    """)
    op.execute(f"""
        CREATE VIEW v_invoices AS
        SELECT id AS invoice_id, vendor_name, vendor_gstin, buyer_name, buyer_gstin,
               invoice_number, invoice_date, place_of_supply,
               subtotal AS taxable_amount, cgst, sgst, igst,
               coalesce(cgst, 0) + coalesce(sgst, 0) + coalesce(igst, 0) AS total_gst,
               total, currency, approved_at
        FROM invoices
        WHERE status = 'approved' AND {USER_FILTER}
    """)
    op.execute(f"""
        CREATE VIEW v_line_items AS
        SELECT li.invoice_id, i.vendor_name, i.invoice_date, li.position, li.description,
               coalesce(li.hsn_sac_code, li.suggested_hsn_code) AS hsn_sac_code,
               li.quantity, li.unit, li.unit_price, li.discount, li.taxable_value, li.gst_rate,
               li.cgst_amount, li.sgst_amount, li.igst_amount, li.line_total
        FROM line_items li
        JOIN invoices i ON i.id = li.invoice_id
        WHERE i.status = 'approved' AND i.{USER_FILTER}
    """)
    # Roles belong to the whole database server, so create it only if it isn't there yet.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledger_reader') THEN
                CREATE ROLE ledger_reader NOLOGIN;
            END IF;
        END
        $$
    """)
    op.execute("GRANT SELECT ON v_invoices, v_line_items TO ledger_reader")
    # Let the app's own database user switch into the read-only role.
    op.execute("GRANT ledger_reader TO CURRENT_USER")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_line_items")
    op.execute("DROP VIEW IF EXISTS v_invoices")
    op.execute("DROP TABLE IF EXISTS ledger_query_context")
    # The role is left in place: it is server-wide and may be used by other databases.
