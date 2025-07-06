def tag_exception(odoo, order_id, reason):
    odoo.comment(order_id, f"[SentinelOps] Exception: {reason}")
    odoo.tag(order_id, "Sentinel-Exception")
