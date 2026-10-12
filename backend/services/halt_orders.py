"""Cancel verified entry buys on halt without touching protective exits.

This path never submits orders or changes execution configuration. Broker
acknowledgements are reported separately from terminal cancellation evidence;
the normal reconciliation paths retain ownership of holdings and partial fills.
"""
import asyncio

from . import options_desk, public_api, public_execution


WORKING = {"NEW", "OPEN", "SUBMITTED", "PENDING", "ACCEPTED", "PARTIALLY_FILLED", "PENDING_NEW"}
TERMINAL = {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "FILLED", "FAILED"}


def _result():
    return {"ok": True, "requested": [], "confirmed": [], "errors": []}


def _status(order):
    return str(order.get("status") or order.get("orderStatus") or "").upper()


def _error(result, reason, order_id=None):
    result["ok"] = False
    result["errors"].append({"reason": reason, "order_id": order_id})


def _opening_buy(order, *, public, result):
    order_id = order.get("orderId") or order.get("id")
    side = str(order.get("orderSide") or order.get("side") or "").upper()
    if side == "SELL" or _status(order) in TERMINAL:
        return False
    if side != "BUY" or _status(order) not in WORKING or not order_id:
        _error(result, "entry_identity_or_status_unverified", order_id)
        return False
    intent = str(order.get("openCloseIndicator") if public else order.get("position_intent") or "").upper()
    if intent in {"CLOSE", "BUY_TO_CLOSE"}:
        return False
    asset = str((order.get("instrument") or {}).get("type") if public else order.get("asset_class") or "").upper()
    if not public and asset != "US_OPTION":
        # The options account may contain equities; this route does not own them.
        return False
    order_class = str(order.get("orderClass") or order.get("order_class") or "SIMPLE").upper()
    if (asset != ("EQUITY" if public else "US_OPTION")
            or intent != ("OPEN" if public else "BUY_TO_OPEN")
            or order_class != "SIMPLE"
            or any(order.get(key) for key in ("legs", "orderLegs", "children", "childOrders", "takeProfit", "stopLoss"))):
        _error(result, "entry_or_nested_exit_binding_unverified", order_id)
        return False
    return True


async def _public_entries():
    result = _result()
    if not public_execution.enabled():
        _error(result, "public_execution_route_unavailable")
        return result
    try:
        async with asyncio.timeout(30), public_api.PublicAPIClient(use_sdk=False) as client:
            payload = await client.search_orders()
            rows = payload.get("orders") if isinstance(payload, dict) else None
            if (not isinstance(rows, list) or len(rows) >= 500
                    or any(payload.get(key) for key in ("nextPageToken", "nextToken", "hasMore"))):
                _error(result, "public_order_coverage_unverified")
                return result
            for order in rows:
                if not isinstance(order, dict):
                    _error(result, "public_order_schema_unverified")
                    continue
                if not _opening_buy(order, public=True, result=result):
                    continue
                order_id = str(order.get("orderId") or order.get("id"))
                try:
                    await client.cancel_order(order_id)
                    result["requested"].append(order_id)
                    # A fill can race cancellation; never call it cancelled or
                    # erase holdings based on the DELETE acknowledgement.
                    status = _status(await client.get_order(order_id))
                    if status in {"CANCELED", "CANCELLED"}:
                        result["confirmed"].append(order_id)
                    else:
                        _error(result, "public_cancel_not_confirmed", order_id)
                except Exception as exc:
                    _error(result, f"public_cancel_unresolved:{exc.__class__.__name__}", order_id)
    except Exception as exc:
        _error(result, f"public_order_lookup_failed:{exc.__class__.__name__}")
    return result


async def _option_entries():
    result = _result()
    route = options_desk.options_account_route_guard()
    if not route.get("ok"):
        _error(result, route.get("reason") or "options_route_unverified")
        return result
    try:
        base = options_desk._options_trade_base()
        async with asyncio.timeout(30), options_desk.httpx.AsyncClient(timeout=15.0, headers=options_desk._options_headers()) as client:
            response = await client.get(f"{base}/v2/orders", params={"status": "open", "limit": 500, "nested": "true"})
            rows = response.json() if response.status_code == 200 else None
            if not isinstance(rows, list) or len(rows) >= 500:
                _error(result, "options_order_coverage_unverified")
                return result
            for order in rows:
                if not isinstance(order, dict):
                    _error(result, "options_order_schema_unverified")
                    continue
                if not _opening_buy(order, public=False, result=result):
                    continue
                order_id = str(order["id"])
                try:
                    deleted = await client.delete(f"{base}/v2/orders/{order_id}")
                    if deleted.status_code not in {200, 202, 204}:
                        _error(result, "options_cancel_rejected", order_id)
                        continue
                    result["requested"].append(order_id)
                    polled = await client.get(f"{base}/v2/orders/{order_id}")
                    if polled.status_code == 200 and _status(polled.json()) in {"CANCELED", "CANCELLED"}:
                        result["confirmed"].append(order_id)
                    else:
                        _error(result, "options_cancel_not_confirmed", order_id)
                except Exception as exc:
                    _error(result, f"options_cancel_unresolved:{exc.__class__.__name__}", order_id)
    except Exception as exc:
        _error(result, f"options_order_lookup_failed:{exc.__class__.__name__}")
    return result


async def cancel_open_entry_orders():
    public = await _public_entries()
    options = await _option_entries()
    return {"ok": public["ok"] and options["ok"], "public": public, "options": options}
