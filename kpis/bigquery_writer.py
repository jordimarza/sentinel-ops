from datetime import datetime
from google.cloud import bigquery

def write_kpis(workflow, result):
    client = bigquery.Client()
    table_id = "your_project.your_dataset.erp_kpis"
    row = {
        "workflow": workflow,
        "timestamp": datetime.utcnow().isoformat(),
        **result
    }
    errors = client.insert_rows_json(table_id, [row])
    if errors:
        print(f"BQ insert errors: {errors}")
