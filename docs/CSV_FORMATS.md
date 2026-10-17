# CSV import formats

Upload UTF-8 CSV with a header row. The UI shows a five-row preview and lets you map source headers to the fields below. Each row needs a source `external_id`; duplicate IDs with identical data are skipped, while conflicting values reject the batch. An accepted batch commits as one transaction. Link IDs refer to records already imported into the **same workspace**.

| Import type | Required mapped fields | Optional fields |
| --- | --- | --- |
| Products | `external_id`, `sku`, `name`, `price` | — |
| Inventory snapshots | `external_id`, `sku`, `location`, `sellable`, `reserved`, `quarantined`, `observed_at` | — |
| Orders | `external_id`, `status`, `channel`, `total`, `placed_at` | `customer_ref` |
| Order items | `external_id`, `order_external_id`, `sku`, `quantity`, `unit_price` | — |
| Returns | `external_id`, `order_external_id`, `sku`, `quantity`, `condition`, `status` | — |
| Payments | `external_id`, `order_external_id`, `amount`, `fee`, `fee_tax`, `captured_at` | — |
| Refunds | `external_id`, `payment_external_id`, `amount`, `refunded_at` | — |
| Settlements | `external_id`, `payment_external_id`, `amount`, `settled_at` | `prior_refund_external_id` |
| Bank transactions | `external_id`, `amount`, `posted_at` | `settlement_external_id` |

Money fields are Indian rupees with up to two decimal places, such as `876.40`, `₹876.40` or `INR 876.40`. A mapped source header ending in `_paise` is read as integer paise. Never mix units within one column. Quantities must be nonnegative integers; order item and return quantities must be positive. Timestamps use ISO 8601, ideally with an explicit offset, for example `2026-10-15T09:00:00+05:30`. Naive timestamps are interpreted as UTC.

For a cross-period refund, import the earlier payment and refund first. Map `prior_refund_external_id` on the later settlement to the existing refund's `external_id`. The deterministic reconciliation subtracts that linked prior refund once; a matching bank credit closes a timing difference. An unlinked or mismatched bank credit remains an exception.

Suggested order: products, orders, order items, inventory snapshots, returns, payments, refunds, settlements, bank transactions. You may omit record types you do not have; the dashboard will show their domains as awaiting import. This is a data import workflow, not a direct provider connection.
