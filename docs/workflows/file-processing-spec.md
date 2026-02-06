# File Processing Workflow Specification

> Automated document creation from Google Drive files via n8n + sentinel-ops.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         GOOGLE DRIVE                                │
│  ┌──────────┐    ┌────────────┐    ┌─────────┐                     │
│  │  inbox/  │───▶│ processed/ │    │ error/  │                     │
│  └──────────┘    └────────────┘    └─────────┘                     │
└───────┬─────────────────────────────────────────────────────────────┘
        │ webhook: file added
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           N8N WORKFLOW                              │
│                                                                     │
│  1. Receive webhook                                                 │
│  2. Extract file metadata (name, owner, last editor)               │
│  3. Route by filename pattern                                       │
│  4. Validate sheet format                                           │
│  5. Parse to JSON (header + lines)                                  │
│  6. Apply preset defaults                                           │
│  7. Call sentinel-ops /validate                                     │
│  8. Call sentinel-ops /execute (create_documents)                   │
│  9. Update sheet with results                                       │
│  10. Send email to owner                                            │
│  11. Move file to processed/ or error/                              │
│                                                                     │
└───────┬─────────────────────────────────────────────────────────────┘
        │ HTTP calls
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      SENTINEL-OPS                                   │
│                                                                     │
│  POST /validate    → Validate JSON without creating                 │
│  POST /execute     → create_documents job                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         ODOO                                        │
│  Creates: sale.order, stock.picking, purchase.order                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Folder Structure

```
📁 ALOHAS File Processing/
├── 📁 inbox/           ← Drop files here for processing
├── 📁 processed/       ← Successfully processed files
├── 📁 error/           ← Files with validation/processing errors
└── 📄 Processing Log   ← Google Sheet with all processing history
```

---

## Filename Routing Patterns

| Pattern | Workflow | Document Type | Description |
|---------|----------|---------------|-------------|
| `*retail_distr*` | Store Replenishment | stock.picking | Transfers from RMT to retail stores |
| `*intercompany*` | Intercompany SO | sale.order | Sales to subsidiary companies |
| `*wholesale_po*` | Wholesale PO | purchase.order | Purchase orders from suppliers |
| `*store_return*` | Store Return | stock.picking | Returns from stores to RMT |

---

## Preset Configurations

### 1. Store Replenishment (`retail_distr`)

Creates internal transfers from central warehouse to retail stores.

```json
{
  "preset_id": "store_replenishment",
  "document_type": "stock.picking",
  "defaults": {
    "picking_type_id": 221,
    "location_id": 557,
    "scheduled_date": "$TODAY",
    "ah_picking_status": "draft"
  },
  "grouping": {
    "by": "partner_name",
    "header_field": "partner_name"
  },
  "partner_mappings": {
    "ALOHAS - BARCELONA": {
      "partner_id": 473644,
      "location_dest_id": 423
    },
    "ALOHAS - MADRID COELLO": {
      "partner_id": 473645,
      "location_dest_id": 422
    },
    "ALOHAS - NYC": {
      "partner_id": 716854,
      "location_dest_id": 577
    },
    "ALOHAS - LA ROCA": {
      "partner_id": 789012,
      "location_dest_id": 600
    }
  }
}
```

### 2. Intercompany Sales Order (`intercompany`)

Creates sales orders to subsidiary companies.

```json
{
  "preset_id": "intercompany_so",
  "document_type": "sale.order",
  "defaults": {
    "pricelist_id": 1672,
    "warehouse_id": 65,
    "payment_term_id": 2,
    "team_id": 5,
    "ah_status": "stock_approved",
    "ah_prepayment_status": "paid",
    "tags": ["intercompany", "auto-import"]
  },
  "grouping": {
    "by": "partner_name",
    "header_field": "partner_name"
  },
  "partner_mappings": {
    "SUNSET (BRITAIN) LTD": {
      "partner_id": 796360,
      "partner_shipping_id": 796360,
      "partner_invoice_id": 796360
    },
    "Sunset Ventures Netherlands B.V": {
      "partner_id": 716686,
      "partner_shipping_id": 716686,
      "partner_invoice_id": 716686
    },
    "Sunset Ventures FRANCE": {
      "partner_id": 453987,
      "partner_shipping_id": 453987,
      "partner_invoice_id": 453987,
      "payment_term_id": 9
    }
  }
}
```

### 3. Wholesale Purchase Order (`wholesale_po`)

Creates purchase orders from suppliers.

```json
{
  "preset_id": "wholesale_po",
  "document_type": "purchase.order",
  "defaults": {
    "picking_type_id": 1,
    "company_id": 1,
    "payment_term_id": 5
  },
  "grouping": {
    "by": "partner_name",
    "header_field": "partner_name"
  }
}
```

---

## Google Sheet Format

### Expected Structure

```
┌─────────────────────────────────────────────────────────────────────┐
│ Row 1: [Headers]                                                    │
│        store_name | product_ref | quantity | notes                  │
├─────────────────────────────────────────────────────────────────────┤
│ Row 2: ALOHAS - BARCELONA | S100303-0238 | 2 |                     │
│ Row 3: ALOHAS - BARCELONA | BTWEC1-3040  | 1 |                     │
│ Row 4: ALOHAS - NYC       | S00054-2538  | 3 |                     │
│ Row 5: ALOHAS - NYC       | S100303-0238 | 1 |                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Required Columns by Workflow

| Workflow | Required Columns | Optional Columns |
|----------|-----------------|------------------|
| `retail_distr` | `store_name`, `product_ref`, `quantity` | `notes` |
| `intercompany` | `partner_name`, `product_ref`, `quantity` | `price_unit`, `commitment_date` |
| `wholesale_po` | `supplier_name`, `product_ref`, `quantity`, `price_unit` | `expected_date` |

### Validation Rules

1. **Headers**: First row must contain expected column names
2. **No empty rows**: Skip empty rows, but flag if > 10% empty
3. **Product refs**: Must match regex `^[A-Z0-9]{2,}-[A-Z0-9]{2,}$` or similar
4. **Quantities**: Must be positive integers
5. **Store/Partner names**: Must exist in preset mappings or be resolvable

---

## Processing Results Format

### Success (written to sheet)

| Column | Example |
|--------|---------|
| `_status` | `success` |
| `_document_id` | `863215` |
| `_document_name` | `RMTRE/OUT/00123` |
| `_document_url` | `https://odoo.alohas.com/web#id=863215&model=stock.picking` |
| `_processed_at` | `2025-01-29 22:15:00` |

### Error (written to sheet)

| Column | Example |
|--------|---------|
| `_status` | `error` |
| `_error_field` | `product_ref` |
| `_error_value` | `INVALID-SKU` |
| `_error_message` | `Product not found` |
| `_processed_at` | `2025-01-29 22:15:00` |

---

## JSON Payload to Sentinel-Ops

### Validation Request

```json
POST /execute
{
  "job": "create_documents",
  "dry_run": true,
  "params": {
    "json_input": {
      "metadata": {
        "source": "n8n",
        "owner": "ops@alohas.com",
        "origin_folder": "gdrive://shared/file-processing/inbox",
        "filename": "retail_distr_2025-01-29.xlsx",
        "file_id": "1abc123xyz",
        "file_modified_at": "2025-01-29T10:30:00Z",
        "total_rows": 32,
        "total_documents": 3
      },
      "documents": [
        {
          "row_number": 2,
          "document_type": "stock.picking",
          "header": {
            "partner_id": 473644,
            "picking_type_id": 221,
            "location_id": 557,
            "location_dest_id": 423,
            "scheduled_date": "2025-01-29",
            "ah_picking_status": "draft"
          },
          "lines": [
            {
              "row_number": 2,
              "product_ref": "S100303-0238",
              "quantity": 2
            },
            {
              "row_number": 3,
              "product_ref": "BTWEC1-3040",
              "quantity": 1
            }
          ]
        },
        {
          "row_number": 4,
          "document_type": "stock.picking",
          "header": {
            "partner_id": 716854,
            "picking_type_id": 221,
            "location_id": 557,
            "location_dest_id": 577,
            "scheduled_date": "2025-01-29",
            "ah_picking_status": "draft"
          },
          "lines": [
            {
              "row_number": 4,
              "product_ref": "S00054-2538",
              "quantity": 3
            },
            {
              "row_number": 5,
              "product_ref": "S100303-0238",
              "quantity": 1
            }
          ]
        }
      ]
    }
  }
}
```

### Creation Request

Same as validation but with `"dry_run": false`.

---

## Idempotency

To prevent duplicate processing:

1. **Check Processing Log** before processing:
   - Lookup by `file_id` + `file_modified_at`
   - If exists with status `success`, skip

2. **Store in metadata**:
   - `file_id`: Google Drive file ID
   - `file_modified_at`: Last modification timestamp

3. **Processing Log columns**:
   - `file_id`
   - `file_name`
   - `file_modified_at`
   - `processed_at`
   - `status` (pending, processing, success, error)
   - `documents_created`
   - `error_message`

---

## Error Handling Strategy

### Validation Errors (Phase 1)

- **All-or-nothing**: If any record fails validation, reject entire file
- Write errors to sheet (per-row)
- Move file to `error/` folder
- Email owner with error summary

### Creation Errors (Phase 2)

- **Best-effort with rollback option**:
  - Option A: All-or-nothing (create none if any fails)
  - Option B: Partial success (create what you can, report failures)

- **Recommendation**: Use Option A for now (simpler, safer)

---

## Email Templates

### Success Email

```
Subject: ✅ File processed: retail_distr_2025-01-29.xlsx

Hi {owner_name},

Your file has been successfully processed.

Summary:
- Documents created: 3
- Total lines: 12

Documents:
1. RMTRE/OUT/00123 (ALOHAS - BARCELONA) - 5 items
   https://odoo.alohas.com/web#id=863215&model=stock.picking

2. RMTRE/OUT/00124 (ALOHAS - NYC) - 4 items
   https://odoo.alohas.com/web#id=863216&model=stock.picking

3. RMTRE/OUT/00125 (ALOHAS - MADRID COELLO) - 3 items
   https://odoo.alohas.com/web#id=863217&model=stock.picking

File moved to: processed/retail_distr_2025-01-29.xlsx

---
Sentinel-Ops Automation
```

### Error Email

```
Subject: ❌ File processing failed: retail_distr_2025-01-29.xlsx

Hi {owner_name},

Your file could not be processed due to validation errors.

Errors found:
- Row 3: Product 'INVALID-SKU' not found
- Row 7: Store 'ALOHAS - UNKNOWN' not in system

Please fix these errors and re-upload the file.

File moved to: error/retail_distr_2025-01-29.xlsx

---
Sentinel-Ops Automation
```

---

## n8n Workflow Nodes

```
1. [Webhook Trigger] Google Drive - File Created in inbox/
        │
        ▼
2. [Google Drive] Get file metadata (name, id, modifiedTime)
        │
        ▼
3. [Google Sheets] Get last editor email
        │
        ▼
4. [Switch] Route by filename pattern
        │
        ├─ *retail_distr* ──▶ [Sub-workflow: Store Replenishment]
        ├─ *intercompany* ──▶ [Sub-workflow: Intercompany SO]
        └─ *wholesale_po* ──▶ [Sub-workflow: Wholesale PO]
        │
        ▼
5. [Set] Load preset configuration
        │
        ▼
6. [Google Sheets] Read all rows
        │
        ▼
7. [Code] Validate sheet format
        │
        ├─ Invalid ──▶ [Error Handler]
        │
        ▼
8. [Code] Parse rows + group by partner + apply preset
        │
        ▼
9. [HTTP] POST sentinel-ops/execute (dry_run=true)
        │
        ├─ Validation failed ──▶ [Error Handler]
        │
        ▼
10. [HTTP] POST sentinel-ops/execute (dry_run=false)
        │
        ├─ Creation failed ──▶ [Error Handler]
        │
        ▼
11. [Google Sheets] Write results to source sheet
        │
        ▼
12. [Google Sheets] Append to Processing Log
        │
        ▼
13. [Google Drive] Move file to processed/
        │
        ▼
14. [Gmail] Send success email to owner

---

[Error Handler]:
    │
    ├─ [Google Sheets] Write errors to source sheet
    ├─ [Google Sheets] Append to Processing Log (status=error)
    ├─ [Google Drive] Move file to error/
    └─ [Gmail] Send error email to owner
```

---

## Future Enhancements

1. **Slack notifications** - Post to #ops-automation channel
2. **Retry mechanism** - Auto-retry transient failures
3. **Batch size limits** - Split large files into chunks
4. **Approval workflow** - Require approval for high-value orders
5. **Scheduling** - Process files at specific times (avoid peak hours)

---

## Open Questions

1. Should we support multiple sheets in one file (one sheet = one document)?
2. What's the max file size / row count we should support?
3. Should partial success be allowed, or all-or-nothing?
4. Who gets the email if the file owner can't be determined?

---

**Version**: 1.0.0
**Created**: 2025-01-29
**Author**: Sentinel-Ops Team
