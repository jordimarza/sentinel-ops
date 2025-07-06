def adjust_qty(odoo, line):
    odoo.write("sale.order.line", line['id'], {"product_uom_qty": line['qty_delivered']})
    line['updated'] = True
