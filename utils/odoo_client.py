def connect():
    # Dummy placeholder for Odoo XML-RPC client
    class OdooClient:
        def search_partial_so_lines_older_than(self, days): return []
        def has_open_moves(self, line_id): return False
        def write(self, model, id_, vals): pass
        def comment(self, id_, note): pass
        def tag(self, id_, tag): pass
    return OdooClient()
