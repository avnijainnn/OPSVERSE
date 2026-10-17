"""Read-only Shopify GraphQL synchronization into workspace source records."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_detection import detect_workspace
from .live_imports import parse_money
from .live_models import InventorySnapshot, Order, OrderItem, Product, ProviderConnection, Refund, Return
from .live_observations import observe, upsert_source
from .shopify_client import ShopifyError, graphql, shop_health

VARIANTS_QUERY = """query SyncVariants($after: String) {
  productVariants(first: 25, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id sku title price updatedAt
      product { id title }
      inventoryItem { id inventoryLevels(first: 25) {
        pageInfo { hasNextPage endCursor }
        nodes { id location { id name }
          quantities(names: ["available", "committed", "reserved", "damaged"]) { name quantity } }
      } }
    }
  }
}"""

ORDERS_QUERY = """query SyncOrders($after: String, $filter: String) {
  orders(first: 25, after: $after, query: $filter, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { id name createdAt updatedAt displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 100) { pageInfo { hasNextPage endCursor }
        nodes { id sku quantity originalUnitPriceSet { shopMoney { amount currencyCode } } } }
      refunds { id createdAt totalRefundedSet { shopMoney { amount currencyCode } } }
      returns(first: 25) { pageInfo { hasNextPage endCursor }
        nodes { id status returnLineItems(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes { ... on ReturnLineItem { id quantity returnReasonDefinition { handle name }
                                      fulfillmentLineItem { lineItem { sku } } } }
      } } }
    }
  }
}"""

VARIANT_LEVELS_QUERY = """query SyncVariantLevels($id: ID!, $after: String) {
  productVariant(id: $id) { id inventoryItem { id inventoryLevels(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id location { id name }
      quantities(names: ["available", "committed", "reserved", "damaged"]) { name quantity } }
  } } }
}"""

ORDER_LINES_QUERY = """query SyncOrderLines($id: ID!, $after: String) {
  order(id: $id) { id lineItems(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id sku quantity originalUnitPriceSet { shopMoney { amount currencyCode } } }
  } }
}"""

ORDER_RETURNS_QUERY = """query SyncOrderReturns($id: ID!, $after: String) {
  order(id: $id) { id returns(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id status returnLineItems(first: 100) {
      pageInfo { hasNextPage endCursor }
      nodes { ... on ReturnLineItem { id quantity returnReasonDefinition { handle name }
        fulfillmentLineItem { lineItem { sku } } } }
    } }
  } }
}"""

RETURN_LINES_QUERY = """query SyncReturnLines($id: ID!, $after: String) {
  return(id: $id) { id returnLineItems(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on ReturnLineItem { id quantity returnReasonDefinition { handle name }
      fulfillmentLineItem { lineItem { sku } } } }
  } }
}"""


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ShopifyError("Shopify returned an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ShopifyError("Shopify returned a timestamp without a timezone")
    return parsed.astimezone(timezone.utc)


def money(value: dict) -> int:
    if value.get("currencyCode") != "INR":
        raise ShopifyError("Shopify store money is not INR; currency conversion is not configured")
    return parse_money(str(value["amount"]))


def page_nodes(db: Session, connection: ProviderConnection, query: str, field: str,
               variables: dict | None = None):
    cursor = None
    cursors: set[str] = set()
    for _ in range(400):
        result = graphql(db, connection, query, {**(variables or {}), "after": cursor}).get(field)
        if not isinstance(result, dict):
            raise ShopifyError(f"Shopify omitted {field} from its response")
        nodes = result.get("nodes")
        info = result.get("pageInfo")
        if (not isinstance(nodes, list) or not isinstance(info, dict) or
                not isinstance(info.get("hasNextPage"), bool)):
            raise ShopifyError(f"Shopify returned malformed {field} pagination")
        yield nodes
        if not info["hasNextPage"]:
            return
        cursor = info.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ShopifyError(f"Shopify returned an invalid {field} page cursor")
        cursors.add(cursor)
    raise ShopifyError(f"Shopify {field} exceeded the configured 10,000-record sync limit")


def nested_nodes(db: Session, connection: ProviderConnection, initial: dict, *,
                 query: str, owner_field: str, owner_id: str, path: tuple[str, ...]) -> list[dict]:
    """Read every page of one nested connection or fail before committing a partial sync."""
    node = initial
    for key in path:
        if not isinstance(node, dict):
            raise ShopifyError(f"Shopify returned malformed {path[-1]} connection")
        node = node.get(key)
    if not isinstance(node, dict) or not isinstance(node.get("nodes"), list):
        raise ShopifyError(f"Shopify returned malformed {path[-1]} connection")
    collected = list(node["nodes"])
    info = node.get("pageInfo")
    if not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
        raise ShopifyError(f"Shopify omitted {path[-1]} pagination metadata")
    cursors: set[str] = set()
    while info.get("hasNextPage"):
        cursor = info.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ShopifyError(f"Shopify returned an invalid {path[-1]} page cursor")
        cursors.add(cursor)
        owner = graphql(db, connection, query, {"id": owner_id, "after": cursor}).get(owner_field)
        if not isinstance(owner, dict) or owner.get("id") != owner_id:
            raise ShopifyError(f"Shopify omitted {owner_field} during nested pagination")
        page = owner
        for key in path:
            if not isinstance(page, dict):
                raise ShopifyError(f"Shopify returned malformed {path[-1]} connection")
            page = page.get(key)
        if (not isinstance(page, dict) or not isinstance(page.get("nodes"), list) or
                not isinstance(page.get("pageInfo"), dict) or
                not isinstance(page["pageInfo"].get("hasNextPage"), bool)):
            raise ShopifyError(f"Shopify returned malformed {path[-1]} pagination")
        collected.extend(page["nodes"])
        if len(collected) > 10_000:
            raise ShopifyError(f"Shopify {path[-1]} exceeded the 10,000-record nested sync limit")
        info = page["pageInfo"]
    return collected


def hydrate_variant(db: Session, connection: ProviderConnection, variant: dict) -> dict:
    item = variant.get("inventoryItem") or {}
    if not item:
        return variant
    levels = nested_nodes(db, connection, variant, query=VARIANT_LEVELS_QUERY,
                          owner_field="productVariant", owner_id=variant["id"],
                          path=("inventoryItem", "inventoryLevels"))
    return {**variant, "inventoryItem": {**item, "inventoryLevels": {"nodes": levels}}}


def hydrate_order(db: Session, connection: ProviderConnection, order: dict) -> dict:
    lines = nested_nodes(db, connection, order, query=ORDER_LINES_QUERY,
                         owner_field="order", owner_id=order["id"],
                         path=("lineItems",))
    returns = nested_nodes(db, connection, order, query=ORDER_RETURNS_QUERY,
                           owner_field="order", owner_id=order["id"],
                           path=("returns",))
    complete_returns = []
    for returned in returns:
        items = nested_nodes(db, connection, returned, query=RETURN_LINES_QUERY,
                             owner_field="return", owner_id=returned["id"],
                             path=("returnLineItems",))
        complete_returns.append({**returned, "returnLineItems": {"nodes": items}})
    return {**order, "lineItems": {"nodes": lines}, "returns": {"nodes": complete_returns}}


def sync_variant(db: Session, workspace_id: str, variant: dict, observed_at: datetime,
                 limitations: set[str], webhook_delivery_id: str | None = None) -> int:
    sku = variant.get("sku")
    if not sku:
        limitations.add("A Shopify variant has no SKU and was skipped")
        return 0
    updated = parse_time(variant["updatedAt"])
    product = variant["product"]
    title = product["title"] + (f" / {variant['title']}" if variant.get("title") not in (None, "Default Title") else "")
    upsert_source(db, model=Product, workspace_id=workspace_id, source_system="shopify",
                  object_kind="product", external_id=variant["id"], event_type="shopify_variant_observed",
                  fields={"sku": sku, "name": title, "price_paise": parse_money(str(variant["price"]))},
                  raw={"variant": {"id": variant["id"], "sku": sku, "title": variant.get("title"),
                                    "price": variant["price"], "updatedAt": variant["updatedAt"]},
                       "product": product}, external_at=updated, correlation_id=product["id"],
                  webhook_delivery_id=webhook_delivery_id)
    count = 0
    for level in (variant.get("inventoryItem") or {}).get("inventoryLevels", {}).get("nodes", []):
        location = level.get("location") or {}
        if not location.get("id") or not location.get("name"):
            limitations.add("An inventory level had no location and was skipped")
            continue
        quantities = {item["name"]: item["quantity"] for item in level.get("quantities", [])}
        if "available" not in quantities:
            limitations.add("An inventory level had no available quantity and was skipped")
            continue
        if "damaged" not in quantities:
            limitations.add("Shopify did not expose damaged quantity for at least one inventory level")
        if "reserved" not in quantities and "committed" not in quantities:
            limitations.add("Shopify did not expose reserved or committed quantity for at least one inventory level")
        external_id = f"{variant['id']}@{level['id']}"
        raw = {"variant_id": variant["id"], "inventory_item_id": variant["inventoryItem"]["id"],
               "level_id": level["id"], "location": location, "quantities": quantities,
               "observed_time_basis": "ingestion"}
        prior = db.scalar(select(InventorySnapshot).where(
            InventorySnapshot.workspace_id == workspace_id,
            InventorySnapshot.source_system == "shopify",
            InventorySnapshot.external_id == external_id))
        if prior and prior.raw == raw:
            continue
        upsert_source(db, model=InventorySnapshot, workspace_id=workspace_id,
                      source_system="shopify", object_kind="inventory", external_id=external_id,
                      event_type="shopify_inventory_observed",
                      fields={"sku": sku, "location": location["name"],
                              "sellable": int(quantities["available"]),
                              "reserved": int(quantities.get("reserved", quantities.get("committed", 0))),
                              "quarantined": int(quantities.get("damaged", 0)),
                              "observed_at": observed_at}, raw=raw, external_at=observed_at,
                      correlation_id=variant["id"], webhook_delivery_id=webhook_delivery_id)
        count += 1
    return count


def sync_order(db: Session, workspace_id: str, source: dict, limitations: set[str],
               webhook_delivery_id: str | None = None) -> None:
    updated = parse_time(source["updatedAt"])
    prior = db.scalar(select(Order).where(Order.workspace_id == workspace_id,
                                          Order.source_system == "shopify",
                                          Order.external_id == source["id"]))
    if prior and prior.source_updated_at:
        previous = prior.source_updated_at.replace(tzinfo=timezone.utc) if prior.source_updated_at.tzinfo is None else prior.source_updated_at
        if updated < previous:
            return
    amount = money(source["totalPriceSet"]["shopMoney"])
    raw = {"id": source["id"], "name": source.get("name"),
           "createdAt": source["createdAt"], "updatedAt": source["updatedAt"],
           "displayFulfillmentStatus": source.get("displayFulfillmentStatus"),
           "totalPriceSet": source["totalPriceSet"]}
    order, _ = upsert_source(db, model=Order, workspace_id=workspace_id,
                             source_system="shopify", object_kind="order", external_id=source["id"],
                             event_type="shopify_order_observed",
                             fields={"customer_ref": None,
                                     "status": str(source.get("displayFulfillmentStatus") or "unknown").lower(),
                                     "channel": "Shopify", "total_paise": amount,
                                     "placed_at": parse_time(source["createdAt"])},
                             raw=raw, external_at=updated, correlation_id=source["id"],
                             webhook_delivery_id=webhook_delivery_id)
    lines = source.get("lineItems", {}).get("nodes", [])
    for line in lines:
        if not line.get("sku"):
            limitations.add("An order line item has no SKU and was skipped")
            continue
        price = money(line["originalUnitPriceSet"]["shopMoney"])
        upsert_source(db, model=OrderItem, workspace_id=workspace_id, source_system="shopify",
                      object_kind="order_item", external_id=line["id"],
                      event_type="shopify_order_item_observed",
                      fields={"order_id": order.id, "sku": line["sku"],
                              "quantity": int(line["quantity"]), "unit_price_paise": price},
                      raw={"line": line, "order_id": source["id"]}, external_at=updated,
                      correlation_id=source["id"], webhook_delivery_id=webhook_delivery_id)
    for refund in source.get("refunds", []):
        refund_amount = money(refund["totalRefundedSet"]["shopMoney"])
        if refund_amount <= 0:
            continue
        upsert_source(db, model=Refund, workspace_id=workspace_id, source_system="shopify",
                      object_kind="refund", external_id=refund["id"],
                      event_type="shopify_refund_observed",
                      fields={"order_id": order.id, "payment_id": None,
                              "amount_paise": refund_amount,
                              "refunded_at": parse_time(refund["createdAt"])},
                      raw={"refund": refund, "order_id": source["id"],
                           "payment_link_unavailable": True},
                      external_at=parse_time(refund["createdAt"]), correlation_id=source["id"],
                      webhook_delivery_id=webhook_delivery_id)
    returns = source.get("returns", {}).get("nodes", [])
    for returned in returns:
        for line in returned.get("returnLineItems", {}).get("nodes", []):
            if not line.get("id"):
                continue
            sku = ((line.get("fulfillmentLineItem") or {}).get("lineItem") or {}).get("sku")
            if not sku:
                limitations.add("A return line item has no linked SKU and was skipped")
                continue
            reason = str((line.get("returnReasonDefinition") or {}).get("handle") or "unknown").lower()
            condition = "damaged" if reason in {"damaged", "defective", "damaged-or-defective"} else "unclassified"
            upsert_source(db, model=Return, workspace_id=workspace_id,
                          source_system="shopify", object_kind="return", external_id=line["id"],
                          event_type="shopify_return_observed",
                          fields={"order_id": order.id, "sku": sku,
                                  "quantity": int(line["quantity"]), "condition": condition,
                                  "status": str(returned.get("status") or "unknown").lower()},
                          raw={"return_id": returned["id"], "line": line,
                               "condition_inferred_from_reason": condition == "damaged"},
                          external_at=updated, correlation_id=source["id"],
                          webhook_delivery_id=webhook_delivery_id)


def sync_shopify(db: Session, connection: ProviderConnection,
                 webhook_delivery_id: str | None = None) -> dict:
    if connection.provider != "shopify" or not connection.access_ciphertext:
        raise ShopifyError("Shopify is not authorized for this workspace")
    workspace_id = connection.workspace_id
    shop = shop_health(db, connection)
    if shop.get("currencyCode") != "INR":
        raise ShopifyError("Shopify store currency is not INR; conversion is not configured")
    start = datetime.now(timezone.utc)
    limitations = set()
    if "read_all_orders" not in set((connection.scopes or "").split(",")):
        limitations.add("Order history is limited to the Shopify window granted by read_orders; older orders need approved read_all_orders access")
    counts = {"products": 0, "inventory": 0, "orders": 0}
    seen_products: set[str] = set()
    seen_inventory: set[str] = set()
    for nodes in page_nodes(db, connection, VARIANTS_QUERY, "productVariants"):
        for node in nodes:
            if not node.get("sku"):
                continue
            node = hydrate_variant(db, connection, node)
            seen_products.add(node["id"])
            levels = (node.get("inventoryItem") or {}).get("inventoryLevels", {}).get("nodes", [])
            seen_inventory.update(f"{node['id']}@{level['id']}" for level in levels if level.get("id"))
            counts["inventory"] += sync_variant(db, workspace_id, node, start, limitations,
                                                  webhook_delivery_id)
            counts["products"] += 1
    for model, kind, seen in ((Product, "product", seen_products),
                              (InventorySnapshot, "inventory", seen_inventory)):
        for stale in db.scalars(select(model).where(model.workspace_id == workspace_id,
                                                   model.source_system == "shopify")).all():
            if stale.external_id in seen:
                continue
            observe(db, workspace_id=workspace_id, source_system="shopify", object_kind=kind,
                    provider_object_id=stale.external_id, event_type="shopify_source_removed",
                    payload={"deleted": True, "source_id": stale.external_id},
                    normalized_record_id=stale.id, external_at=start,
                    webhook_delivery_id=webhook_delivery_id)
            db.delete(stale)
    db.flush()
    previous = connection.last_sync_at
    if previous:
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        search = "updated_at:>=" + (previous - timedelta(minutes=5)).isoformat()
    else:
        search = None
    for nodes in page_nodes(db, connection, ORDERS_QUERY, "orders", {"filter": search}):
        for node in nodes:
            node = hydrate_order(db, connection, node)
            sync_order(db, workspace_id, node, limitations, webhook_delivery_id)
            counts["orders"] += 1
    detect_workspace(db, workspace_id)
    connection.last_sync_at = start
    connection.sync_cursor = {"limitations": sorted(limitations), "api_version": "2026-10"}
    connection.status = "connected"
    connection.last_error = None
    db.commit()
    return {"counts": counts, "last_successful_sync_at": start.isoformat(),
            "limitations": sorted(limitations)}
