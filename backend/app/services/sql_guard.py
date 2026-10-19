"""Check model-written SQL before it runs (blueprint section 11).

The rule is an allow-list, not a block-list: a query passes only if it is
- exactly one statement, and a SELECT (or a UNION of SELECTs),
- reading only the ledger views (or its own WITH sub-queries),
- calling only well-known harmless functions (sums, counts, dates, rounding),
and it always gets a row limit. Anything else is rejected with a reason.

This is the first wall. The second is the database itself: the query runs as a
read-only role that can only see the two views, filtered to the current user.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_VIEWS = {"v_invoices", "v_line_items"}
MAX_ROWS = 200

# Lower-case function names as sqlglot reports them (sql_name() or the raw name).
ALLOWED_FUNCTIONS = {
    # aggregates
    "sum",
    "count",
    "avg",
    "min",
    "max",
    # numbers and text
    "round",
    "abs",
    "coalesce",
    "nullif",
    "greatest",
    "least",
    "lower",
    "upper",
    "trim",
    "length",
    "concat",
    "if",
    "case",
    # dates (sqlglot names some of these differently from Postgres)
    "extract",
    "date_trunc",
    "timestamp_trunc",
    "current_date",
    "current_timestamp",
    "to_char",
    "time_to_str",  # sqlglot's name for TO_CHAR(date, format); the line above never matched
    "make_date",  # MAKE_DATE(2026, 4, 1): e.g. the start of a financial year
    "to_date",
    "str_to_date",  # sqlglot's name for TO_DATE(text, format)
    "date_part",
    "date",
    "cast",
    "try_cast",
    "interval",
    "dateadd",
    "date_add",
    "date_sub",
    "date_diff",
    "datediff",
    "timestampadd",
    "timestamp_add",
    "timestampsub",
    "timestamp_sub",
}

# Statements and clauses that must never appear anywhere in the tree.
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Into,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.TruncateTable,
    exp.Copy,
    exp.Grant,
    exp.Lock,
)


class UnsafeSQL(ValueError):
    """The query was rejected; the message says why (shown to the user)."""


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return node.name.lower()
    return node.sql_name().lower()


def check_sql(sql: str) -> str:
    """Return a safe version of `sql` (with a row limit), or raise UnsafeSQL."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError:
        raise UnsafeSQL("The query could not be parsed") from None
    if len(statements) != 1:
        raise UnsafeSQL("Only a single statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.Union | exp.Intersect | exp.Except):
        raise UnsafeSQL("Only SELECT queries are allowed")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQL(f"{type(node).__name__} is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog:
            raise UnsafeSQL(f"Schema-qualified names are not allowed: {table.sql()}")
        name = table.name.lower()
        if name not in ALLOWED_VIEWS and name not in cte_names:
            raise UnsafeSQL(f"Only the ledger views can be queried, not '{table.name}'")

    for function in tree.find_all(exp.Func):
        if isinstance(function, exp.Connector):
            continue  # sqlglot models AND / OR as functions; they are plain logic
        name = _function_name(function)
        if name not in ALLOWED_FUNCTIONS and not isinstance(function, exp.AggFunc | exp.Case):
            raise UnsafeSQL(f"The function '{name}' is not allowed")

    # Always cap the rows returned.
    if isinstance(tree, exp.Select):
        current = tree.args.get("limit")
        limit_value = current.expression if current is not None else None
        if not (isinstance(limit_value, exp.Literal) and int(limit_value.this) <= MAX_ROWS):
            tree = tree.limit(MAX_ROWS)
    else:  # a UNION etc.: wrap it
        tree = exp.select("*").from_(tree.subquery("q")).limit(MAX_ROWS)
    return tree.sql(dialect="postgres")
