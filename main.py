from workflows import sales_order_cleanup
from utils import odoo_client, logger, kpi_writer

if __name__ == "__main__":
    odoo = odoo_client.connect()
    log = logger.get_logger("sales_order_cleanup")
    results = sales_order_cleanup.run(odoo, log)
    kpi_writer.write_kpis("sales_order_cleanup", results)
