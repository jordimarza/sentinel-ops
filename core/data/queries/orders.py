"""
Order-Related BigQuery Queries

SQL queries for order data discovery.
These assume your BQ dataset has tables synced from Odoo.

Table expectations:
- sale_order: id, name, ah_status, state, date_order
- sale_order_line: id, name, order_id, product_id, product_uom_qty, qty_delivered
"""

from typing import Optional


def orders_with_qty_mismatch_sql(
    project: str,
    dataset: str,
    ah_statuses: list[str],
    limit: Optional[int] = None,
    order_ids: Optional[list[int]] = None,
    days: Optional[int] = None,
    order_name_pattern: Optional[str] = None,
    exclude_product_ids: Optional[list[int]] = None,
    # Table names (configurable for different BQ setups)
    order_table: str = "sale_order",
    line_table: str = "sale_order_line",
) -> str:
    """
    Generate SQL to find orders with qty mismatch.

    Returns lines where product_uom_qty != qty_delivered on closed orders.

    Args:
        project: GCP project ID
        dataset: BigQuery dataset name
        ah_statuses: List of ah_status values to filter
        limit: Max lines to return
        order_ids: Optional specific order IDs
        days: Only orders from last N days
        order_name_pattern: Pattern for order name (use % as wildcard)
        exclude_product_ids: Product IDs to exclude
        order_table: Name of order table in BQ
        line_table: Name of line table in BQ

    Returns:
        SQL query string
    """
    # Format values for SQL
    status_list = ", ".join(f"'{s}'" for s in ah_statuses)

    # Build WHERE clauses
    where_clauses = [
        f"o.ah_status IN ({status_list})",
        "o.state = 'sale'",
        "l.qty_delivered != l.product_uom_qty",
        "l.qty_delivered >= 0",
    ]

    if order_ids:
        ids_list = ", ".join(str(i) for i in order_ids)
        where_clauses.append(f"o.id IN ({ids_list})")

    if days:
        where_clauses.append(
            f"o.date_order >= DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY)"
        )

    if order_name_pattern:
        # Convert SQL LIKE pattern (already uses %)
        where_clauses.append(f"o.name LIKE '{order_name_pattern}'")

    if exclude_product_ids:
        ids_list = ", ".join(str(i) for i in exclude_product_ids)
        where_clauses.append(f"l.product_id NOT IN ({ids_list})")

    where_sql = "\n  AND ".join(where_clauses)
    limit_sql = f"LIMIT {limit}" if limit else ""

    sql = f"""
SELECT
    o.id AS order_id,
    o.name AS order_name,
    o.ah_status,
    l.id AS line_id,
    l.name AS line_name,
    l.product_id,
    l.product_uom_qty AS ordered_qty,
    l.qty_delivered AS delivered_qty
FROM `{project}.{dataset}.{line_table}` l
JOIN `{project}.{dataset}.{order_table}` o ON l.order_id = o.id
WHERE {where_sql}
ORDER BY o.date_order DESC, o.id, l.id
{limit_sql}
"""
    return sql.strip()


def order_summary_sql(
    project: str,
    dataset: str,
    order_id: int,
    order_table: str = "sale_order",
    line_table: str = "sale_order_line",
) -> str:
    """
    Generate SQL to get order summary with line totals.

    Useful for debugging and verification.
    """
    return f"""
SELECT
    o.id AS order_id,
    o.name AS order_name,
    o.ah_status,
    o.state,
    o.date_order,
    COUNT(l.id) AS line_count,
    SUM(l.product_uom_qty) AS total_ordered,
    SUM(l.qty_delivered) AS total_delivered,
    SUM(CASE WHEN l.product_uom_qty != l.qty_delivered THEN 1 ELSE 0 END) AS mismatched_lines
FROM `{project}.{dataset}.{order_table}` o
LEFT JOIN `{project}.{dataset}.{line_table}` l ON l.order_id = o.id
WHERE o.id = {order_id}
GROUP BY o.id, o.name, o.ah_status, o.state, o.date_order
"""


def stale_candidates_check_sql(
    project: str,
    dataset: str,
    line_ids: list[int],
    line_table: str = "sale_order_line",
) -> str:
    """
    Generate SQL to check if BQ data matches current line state.

    Use after fetching candidates to see how stale the BQ data is.
    """
    ids_list = ", ".join(str(i) for i in line_ids)
    return f"""
SELECT
    id AS line_id,
    product_uom_qty AS bq_ordered,
    qty_delivered AS bq_delivered,
    _sdc_extracted_at AS last_synced  -- Stitch/Fivetran timestamp
FROM `{project}.{dataset}.{line_table}`
WHERE id IN ({ids_list})
"""
