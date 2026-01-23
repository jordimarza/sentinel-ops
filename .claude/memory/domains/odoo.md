# Odoo Learnings

> Odoo-specific knowledge for sentinel-ops.

---

## XML-RPC API

### Authentication

```python
common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, username, password, {})
```

### Execute Methods

```python
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
result = models.execute_kw(db, uid, password, model, method, args, kwargs)
```

---

## Common Models

| Model | Purpose |
|-------|---------|
| `sale.order` | Sales orders |
| `sale.order.line` | Sales order lines |
| `stock.move` | Stock movements |
| `stock.picking` | Transfers/pickings |
| `product.product` | Products |
| `res.partner` | Customers/vendors |

---

## Useful Fields

### sale.order.line
- `product_uom_qty` - Ordered quantity
- `qty_delivered` - Delivered quantity
- `qty_invoiced` - Invoiced quantity
- `order_id` - Parent order (Many2one)

### stock.move
- `state` - waiting, confirmed, assigned, done, cancel
- `sale_line_id` - Related SO line
- `product_uom_qty` - Planned quantity
- `quantity_done` - Actual moved

---

## Tag Management

Adding tags uses special syntax:
```python
# (4, id) = Link existing record
self.odoo.write(model, ids, {"tag_ids": [(4, tag_id)]})

# (0, 0, vals) = Create and link
# (3, id) = Unlink
# (5, 0, 0) = Unlink all
# (6, 0, [ids]) = Replace all
```

---

## Message Posting

```python
self.odoo.execute(
    "sale.order",
    "message_post",
    [record_id],
    body="<p>HTML message</p>",
    message_type="notification",  # or "comment"
)
```

---

## Stock Move States

```
draft → waiting → confirmed → assigned → done
                                      ↘ cancel
```

- `waiting`: Waiting for another move
- `confirmed`: Waiting for availability
- `assigned`: Reserved, ready to process
- `done`: Completed
- `cancel`: Cancelled

---

**Last updated**: 2025-01-22
