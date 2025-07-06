def run(odoo, log):
    from tasks.find_old_partial_orders import run as find_lines
    from tasks.check_open_moves import has_open_moves
    from actions.adjust_so_line_qty import adjust_qty
    from actions.tag_and_note import tag_exception

    results = []
    for line in find_lines(odoo):
        try:
            if not has_open_moves(odoo, line):
                adjust_qty(odoo, line)
                log.success(line['id'], "Adjusted to delivered qty")
                line['updated'] = True
            else:
                log.skip(line['id'], "Has open stock moves")
        except Exception as e:
            tag_exception(odoo, line['order_id'], str(e))
            log.error(line['id'], str(e))
            line['error'] = str(e)
        results.append(line)

    return {
        "lines_checked": len(results),
        "lines_updated": sum(1 for r in results if r.get("updated")),
        "exceptions": sum(1 for r in results if r.get("error")),
    }
