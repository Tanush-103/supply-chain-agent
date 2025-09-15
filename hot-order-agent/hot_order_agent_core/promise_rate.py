#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
S/4HANA on-prem → Promise Rate Agent (Python)
- Fetch Sales Order schedule lines via OData V2 (API_SALES_ORDER_SRV)
- Optional ATP re-check via PyRFC (BAPI_MATERIAL_AVAILABILITY)
- Compute item-weighted promise rate
"""

from __future__ import annotations
import os
import json
import time
import logging
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation, getcontext
from typing import Dict, Iterable, List, Optional, Tuple
from dotenv import load_dotenv
import datetime
import re
import pandas as pd


load_dotenv()

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlencode, quote



# ---------------------------
# Configuration (env-driven)
# ---------------------------

# Base like: https://<gw-host>/sap/opu/odata/sap/API_SALES_ORDER_SRV
S4_BASE_URL = os.getenv("S4_BASE_URL", "").rstrip("/")
# Auth mode: "OAUTH" or "BASIC"
S4_AUTH_MODE = os.getenv("S4_AUTH_MODE", "OAUTH").upper()
# For OAuth2 (client credentials)
S4_OAUTH_TOKEN_URL = os.getenv("S4_OAUTH_TOKEN_URL", "")
S4_OAUTH_CLIENT_ID = os.getenv("S4_OAUTH_CLIENT_ID", "")
S4_OAUTH_CLIENT_SECRET = os.getenv("S4_OAUTH_CLIENT_SECRET", "")
S4_OAUTH_SCOPE = os.getenv("S4_OAUTH_SCOPE", "")  # optional
S4_CHANGED_FIELD = os.getenv("S4_CHANGED_FIELD", "LastChangeDateTime")
S4_SO_CHANGED_FIELD = os.getenv("S4_SO_CHANGED_FIELD", "LastChangeDateTime")
S4_SINCE_LITERAL_KIND = os.getenv("S4_SINCE_LITERAL_KIND", "DATETIMEOFFSET").upper()
ATP_BACKEND = os.getenv("ATP_BACKEND", "NONE").upper()
S4_AATP_CHECK_URL = os.getenv("S4_AATP_CHECK_URL", "").strip()


# For Basic
S4_BASIC_USER = os.getenv("S4_BASIC_USER", "")
S4_BASIC_PASS = os.getenv("S4_BASIC_PASS", "")

# TLS
REQUESTS_VERIFY = os.getenv("REQUESTS_VERIFY", "true").lower() != "false"
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SEC", "30"))

# Optional: which datetime field to use for "recently promised" filtering.
# If your schedule-line entity doesn’t expose LastChangeDateTime, point this to a supported field or leave blank to skip.
S4_CHANGED_FIELD = os.getenv("S4_CHANGED_FIELD", "LastChangeDateTime")

# Optional: PyRFC (BAPI) connection params for ATP re-check
# Requires SAP NW RFC SDK installed and pyrfc available.
PYRFC_PARAMS = {
    "ashost": os.getenv("RFC_ASHOST", ""),      # /mshost & group for load-balanced systems
    "sysnr": os.getenv("RFC_SYSNR", ""),
    "client": os.getenv("RFC_CLIENT", ""),
    "user":   os.getenv("RFC_USER", ""),
    "passwd": os.getenv("RFC_PASSWD", ""),
    "lang":   os.getenv("RFC_LANG", "EN"),
}

# Precision for Decimal ops
getcontext().prec = 28

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("promise-rate-agent")


# ---------------------------
# Data models
# ---------------------------

@dataclass
class ScheduleLine:
    salesOrder: str
    salesOrderItem: str
    scheduleLine: str
    scheduleLineOrderQuantity: Decimal
    orderQuantityUnit: Optional[str]
    confdOrderQtyByMatlAvailCheck: Decimal
    requestedDeliveryDate: Optional[str]
    confirmedDeliveryDate: Optional[str]
    # Optional payload from ATP re-check
    atpCheck: Optional[Dict] = None


@dataclass
class OrderSummary:
    orderId: str
    orderedQty: Decimal
    confirmedQty: Decimal
    orderPromiseRate: Decimal
    items: List[Dict]


# ---------------------------
# HTTP session & auth
# ---------------------------

def _retrying_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.3,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PATCH", "PUT", "DELETE"])
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s

def _oauth_token(session: requests.Session) -> str:
    if not S4_OAUTH_TOKEN_URL:
        raise RuntimeError("S4_OAUTH_TOKEN_URL missing for OAUTH mode.")
    data = {"grant_type": "client_credentials"}
    if S4_OAUTH_SCOPE:
        data["scope"] = S4_OAUTH_SCOPE
    resp = session.post(
        S4_OAUTH_TOKEN_URL,
        data=data,
        auth=(S4_OAUTH_CLIENT_ID, S4_OAUTH_CLIENT_SECRET),
        timeout=REQUEST_TIMEOUT,
        verify=REQUESTS_VERIFY,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def _auth_headers(session: requests.Session) -> Dict[str, str]:
    if S4_AUTH_MODE == "BASIC":
        # requests will handle Basic via auth=..., but we only use headers here for simplicity
        return {}
    # OAuth
    token = _oauth_token(session)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------
# OData helpers
# ---------------------------

def _odata_get_all(session: requests.Session, url: str, headers: Dict[str, str]) -> List[Dict]:
    """Follow SAP OData V2 paging (__next or @odata.nextLink)."""
    all_rows: List[Dict] = []

    while True:
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, verify=REQUESTS_VERIFY)
        # Retry without since-filter if server rejects it (e.g., 400 due to field not present)
        if resp.status_code == 400 and "LastChangeDateTime" in url:
            log.warning("400 on OData call with LastChangeDateTime filter; retrying without 'since' filter.")
            # Strip the since filter heuristically (best effort)
            url = _strip_since_filter(url)
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, verify=REQUESTS_VERIFY)

        resp.raise_for_status()
        data = resp.json()

        # SAP GW V2 usually returns under key "d"
        if "d" in data:
            d = data["d"]
            if "results" in d:
                rows = d["results"]
                all_rows.extend(rows)
                next_link = d.get("__next")
                if next_link:
                    url = next_link
                    continue
                break
            else:
                # Single entity?
                all_rows.append(d)
                break
        else:
            # Some gateways provide OData v2-like payload with @odata.nextLink
            value = data.get("value", [])
            all_rows.extend(value)
            next_link = data.get("@odata.nextLink")
            if next_link:
                url = next_link
                continue
            break

    return all_rows

def _strip_since_filter(url: str) -> str:
    # naive: remove " and LastChangeDateTime ge datetimeoffset'...'"
    return url.replace(" and LastChangeDateTime ge ", " ").split("datetimeoffset'")[0] if "LastChangeDateTime" in url else url


# ---------------------------
# Domain logic
# ---------------------------



def fetch_schedule_lines(
    order_ids: Iterable[str],
    since_iso: Optional[str] = None,
) -> List[ScheduleLine]:
    """
    Calls API_SALES_ORDER_SRV → A_SalesOrderScheduleLine and returns parsed schedule lines.
    """
    if not S4_BASE_URL:
        raise RuntimeError("S4_BASE_URL is not configured.")

    entity = f"{S4_BASE_URL}/A_SalesOrderScheduleLine"
    entity1 = f"{S4_BASE_URL}/A_SalesOrder"
    #print(entity1)
    # Build $filter: (SalesOrder eq '...' or SalesOrder eq '...')
    or_terms = [f"SalesOrder eq '{quote(str(x))}'" for x in order_ids]
    if not or_terms:
        return []

    filt = "(" + " or ".join(or_terms) + ")"
    if since_iso and S4_CHANGED_FIELD:
        # OData V2 datetimeoffset literal
        filt += f" and {S4_CHANGED_FIELD} ge datetimeoffset'{since_iso}'"

    select_fields = [
        "SalesOrder",
        "SalesOrderItem",
        "ScheduleLine",
        "ScheduleLineOrderQuantity",
        "OrderQuantityUnit",
        "ConfdOrderQtyByMatlAvailCheck",
        "RequestedDeliveryDate",
        "ConfirmedDeliveryDate",
    ]

    params = {
        "$filter": filt,
        "$select": ",".join(select_fields)
    }

    session = _retrying_session()
    headers = {
        "Accept": "application/json",
        **_auth_headers(session),
    }

    # For Basic auth, pass via session.auth
    if S4_AUTH_MODE == "BASIC":
        session.auth = (S4_BASIC_USER, S4_BASIC_PASS)

    url = f"{entity}?{urlencode(params)}"
    rows = _odata_get_all(session, url, headers)

    def d(x: Optional[str]) -> Decimal:
        try:
            return Decimal(x) if x is not None else Decimal("0")
        except InvalidOperation:
            return Decimal("0")

    sls: List[ScheduleLine] = []
    for r in rows:
        # V2 payloads often nest under r[...] directly
        sls.append(
            ScheduleLine(
                salesOrder=r.get("SalesOrder"),
                salesOrderItem=r.get("SalesOrderItem"),
                scheduleLine=r.get("ScheduleLine"),
                scheduleLineOrderQuantity=d(r.get("ScheduleLineOrderQuantity")),
                orderQuantityUnit=r.get("OrderQuantityUnit"),
                confdOrderQtyByMatlAvailCheck=d(r.get("ConfdOrderQtyByMatlAvailCheck")),
                requestedDeliveryDate=r.get("RequestedDeliveryDate"),
                confirmedDeliveryDate=r.get("ConfirmedDeliveryDate"),
            )
        )
    return sls


def optional_atp_recheck_with_pyrfc(lines: List[ScheduleLine]) -> None:
    """
    Optional: enrich each line with a quick ATP re-check via BAPI_MATERIAL_AVAILABILITY.
    Requires `pyrfc` + SAP NW RFC SDK installed & accessible. Safe no-op if not present or not configured.
    """
    try:
        from pyrfc import Connection
    except Exception as e:
        log.info("PyRFC not available; skipping ATP re-check. (%s)", e)
        return

    # Gate on minimal params
    if not PYRFC_PARAMS.get("ashost") and not PYRFC_PARAMS.get("mshost"):
        log.info("PyRFC connection params not configured; skipping ATP re-check.")
        return

    # WARNING: We need MATERIAL/PLANT/REQ_QTY/DATE to do a meaningful ATP.
    # Those fields are not part of schedule line by default; you likely have them on item or via an expansion.
    # This stub shows the pattern; adapt the mapping to your data model.
    conn = Connection(**{k: v for k, v in PYRFC_PARAMS.items() if v})
    try:
        for sl in lines:
            # TODO: Look up material/plant/req qty/date for this schedule line.
            # Minimal illustrative call:
            bapi_resp = conn.call(
                "BAPI_MATERIAL_AVAILABILITY",
                MATERIAL="",       # e.g., from the order item
                PLANT="",          # plant
                UNIT=sl.orderQuantityUnit or "",
                REQ_QTY=str(sl.scheduleLineOrderQuantity),
                REQ_DATE=sl.requestedDeliveryDate.replace("-", "") if sl.requestedDeliveryDate else "",
                CHECK_RULE="",     # if used in your setup
                STOCK_CHECK="X",
            )
            # Commonly, table WMDVEX carries detailed confirmations
            sl.atpCheck = {
                "RETURN": bapi_resp.get("RETURN"),
                "WMDVEX": bapi_resp.get("WMDVEX"),
            }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def compute_item_weighted(lines: List[ScheduleLine]) -> Dict:
    # Group by order
    by_order: Dict[str, List[ScheduleLine]] = {}
    for sl in lines:
        by_order.setdefault(sl.salesOrder, []).append(sl)

    def sum_dec(values: Iterable[Decimal]) -> Decimal:
        s = Decimal("0")
        for v in values:
            s += v
        return s

    orders: List[OrderSummary] = []
    for order_id, sls in by_order.items():
        ordered = sum_dec(sl.scheduleLineOrderQuantity for sl in sls)
        confirmed = sum_dec(sl.confdOrderQtyByMatlAvailCheck for sl in sls)
        rate = (confirmed / ordered) if ordered != 0 else Decimal("0")
        orders.append(
            OrderSummary(
                orderId=order_id,
                orderedQty=ordered,
                confirmedQty=confirmed,
                orderPromiseRate=rate,
                items=[
                    {
                        "item": sl.salesOrderItem,
                        "scheduleLine": sl.scheduleLine,
                        "orderedQty": float(sl.scheduleLineOrderQuantity),
                        "confirmedQty": float(sl.confdOrderQtyByMatlAvailCheck),
                        "unit": sl.orderQuantityUnit,
                        "requestedDeliveryDate": sl.requestedDeliveryDate,
                        "confirmedDeliveryDate": sl.confirmedDeliveryDate,
                        **({"atpCheck": sl.atpCheck} if sl.atpCheck else {}),
                    }
                    for sl in sls
                ],
            )
        )

    ordered_total = sum((o.orderedQty for o in orders), Decimal("0"))
    confirmed_total = sum((o.confirmedQty for o in orders), Decimal("0"))
    item_weighted_rate = (confirmed_total / ordered_total) if ordered_total != 0 else Decimal("0")

    return {
        "orders": [
            {
                "orderId": o.orderId,
                "orderedQty": float(o.orderedQty),
                "confirmedQty": float(o.confirmedQty),
                "orderPromiseRate": float(o.orderPromiseRate),
                "items": o.items,
            }
            for o in orders
        ],
        "aggregate": {
            "orderedTotal": float(ordered_total),
            "confirmedTotal": float(confirmed_total),
            "itemWeightedPromiseRate": float(item_weighted_rate),
        },
    }


# ---------------------------
# Public API (what your agent calls)
# ---------------------------

def _fetch_item_context(order_item_pairs: list[tuple[str, str]]):
    """
    Returns { (salesOrder, salesOrderItem) : { 'Material': ..., 'ProductionPlant': ..., 'OrderQuantityUnit': ... } }
    """
    #base = _normalize_s4_base_url(S4_BASE_URL)
    entity = f"{S4_BASE_URL}/A_SalesOrderItem"
    session = _retrying_session()
    headers = {"Accept": "application/json", **_auth_headers(session)}
    if S4_AUTH_MODE == "BASIC":
        session.auth = (S4_BASIC_USER, S4_BASIC_PASS)

    # Build OR filter like: (SalesOrder eq '...' and SalesOrderItem eq '...') or ...
    def chunk(lst, n): 
        for i in range(0, len(lst), n): 
            yield lst[i:i+n]

    ctx = {}
    select = "SalesOrder,SalesOrderItem,Material,ProductionPlant,OrderQuantityUnit"
    for batch in chunk(order_item_pairs, 60):
        ors = [f"(SalesOrder eq '{so}' and SalesOrderItem eq '{it}')" for so,it in batch]
        params = {"$filter": "(" + " or ".join(ors) + ")", "$select": select}
        url = f"{entity}?{urlencode(params)}"
        for r in _odata_get_all(session, url, headers):
            k = (r.get("SalesOrder"), r.get("SalesOrderItem"))
            ctx[k] = {
                "Material": r.get("Material"),
                "ProductionPlant": r.get("ProductionPlant"),
                "OrderQuantityUnit": r.get("OrderQuantityUnit")
            }
    return ctx


def optional_atp_recheck_via_aatp_http(lines: list[ScheduleLine]) -> None:
    """
    Re-check ATP via HTTP aATP endpoint. 
    You MUST set S4_AATP_CHECK_URL to the concrete endpoint in your system.
    Payload shape varies slightly by release; adjust keys in 'items' if needed.
    This function enriches each ScheduleLine with sl.atpCheck = {...}.
    """
    if not S4_AATP_CHECK_URL:
        log.info("S4_AATP_CHECK_URL not set; skipping aATP HTTP re-check.")
        return

    # 1) Gather item context (material/plant/unit) for each schedule line
    pairs = [(sl.salesOrder, sl.salesOrderItem) for sl in lines]
    item_ctx = _fetch_item_context(pairs)
    # entity = f"{S4_AATP_CHECK_URL}/A_ATPChkRlvtProductPlant"
    # material_terms = [f"SalesOrder eq '{oid}'" for oid in order_ids]

    # 2) Build request payload
    req_items = []
    for sl in lines:
        key = (sl.salesOrder, sl.salesOrderItem)
        ctx = item_ctx.get(key, {})
        if not ctx.get("Material") or not ctx.get("ProductionPlant"):
            continue  # cannot check ATP without material/plant
        req_items.append({
            # --- Adjust these keys to match your aATP service contract ---
            "material": ctx["Material"],
            "plant": ctx["ProductionPlant"],
            "requestedQuantity": str(sl.scheduleLineOrderQuantity),  # as string to be safe
            "uom": ctx.get("OrderQuantityUnit") or sl.orderQuantityUnit or "",
            "requestedDate": (sl.requestedDeliveryDate or "").split("T")[0],  # YYYY-MM-DD
        })

    if not req_items:
        log.info("No items eligible for aATP HTTP re-check.")
        return

    session = _retrying_session()
    headers = {"Accept": "application/json", "Content-Type": "application/json", **_auth_headers(session)}
    if S4_AUTH_MODE == "BASIC":
        session.auth = (S4_BASIC_USER, S4_BASIC_PASS)

    payload = {"items": req_items}  # ← rename to the exact root your API expects
   
    try:
        resp = session.post(S4_AATP_CHECK_URL, headers=headers, data=json.dumps(payload),
                            timeout=REQUEST_TIMEOUT, verify=REQUESTS_VERIFY)
        if resp.status_code >= 400:
            log.warning("aATP HTTP returned %s: %s", resp.status_code, resp.text[:500])
            return
        data = resp.json()
    except Exception as e:
        log.warning("aATP HTTP call failed: %s", e)
        return

    # 3) Attach per-line evidence (this depends on your API's response shape)
    # We assume the response echoes items in order; adapt lookup if it returns keys.
    for sl, result in zip([x for x in lines if (x.salesOrder, x.salesOrderItem) in item_ctx], data.get("items", [])):
        sl.atpCheck = result  # keep raw; or map {availableQty, confirmedDate, ...}



def get_promise_rate(
    order_ids: List[str],
    since_iso: Optional[str] = None,
    fresh_atp: bool = False,
) -> Dict:
    """
    Main entry point.
    - order_ids: explicit Sales Order IDs
    - since_iso: ISO8601 lower bound for "recently promised" (uses S4_CHANGED_FIELD if configured)
    - fresh_atp: if True and PyRFC is available, re-check ATP for each schedule line
    """
    lines = fetch_schedule_lines(order_ids, since_iso=since_iso)
    
    if fresh_atp and lines:
        
        if ATP_BACKEND == "AATP_HTTP":
            optional_atp_recheck_via_aatp_http(lines)
        # elif ATP_BACKEND == "BAPI_SOAP":
        #     optional_atp_recheck_via_bapi_soap(lines)
        else:
            log.info("ATP_BACKEND=%s → skipping fresh ATP re-check.", ATP_BACKEND)
    # if fresh_atp:
    #     optional_atp_recheck_with_pyrfc(lines)

    result = compute_item_weighted(lines)
    result["meta"] = {
        "source": "API_SALES_ORDER_SRV.A_SalesOrderScheduleLine",
        "filters": ({ "since": since_iso } if since_iso else {}),
        "atp_backend": ATP_BACKEND     
                    
    }
    return result

def _since_literal(field: str, iso: str) -> str:
    # Normalize ISO '...Z' and strip fractional seconds
    ts = iso.replace("Z", "")
    try:
        dt = datetime.fromisoformat(ts)
        clean = dt.replace(microsecond=0).isoformat()
    except Exception:
        clean = ts.split(".")[0]
    if S4_SINCE_LITERAL_KIND == "DATETIME":
        return f"{field} ge datetime'{clean}'"
    return f"{field} ge datetimeoffset'{clean}Z'"


def _entity_has_property(session: requests.Session, base_url: str, entity_set: str, prop: str, headers: dict) -> bool:
    try:
        meta = session.get(f"{base_url}/$metadata", headers=headers, timeout=REQUEST_TIMEOUT, verify=REQUESTS_VERIFY)
        meta.raise_for_status()
        return (entity_set in meta.text) and (f'Name="{prop}"' in meta.text)
    except Exception:
        return True  # don't block if metadata is restricted


def list_sales_orders(
    since_iso: Optional[str] = None,
    sales_org: Optional[str] = None,
    sold_to: Optional[str] = None,
    distribution_channel: Optional[str] = None,
    division: Optional[str] = None,
    max_results: int = 100,
    order_by: str = "SalesOrder desc",
) -> List[Dict]:
    """
    Returns a list of Sales Orders from A_SalesOrder with optional filters.
    Fields returned: SalesOrder, CreationDate, LastChangeDateTime, SalesOrganization,
                     DistributionChannel, OrganizationDivision, SoldToParty, SalesOrderType,
                     OverallSDProcessStatus
    """
    if not S4_BASE_URL:
        raise RuntimeError("S4_BASE_URL is not configured.")
    #base = _normalize_s4_base_url(S4_BASE_URL)
    entity = f"{S4_BASE_URL}/A_SalesOrder"
    #entity = f"{base}/A_SalesOrder"

    session = _retrying_session()
    headers = {"Accept": "application/json", **_auth_headers(session)}
    if S4_AUTH_MODE == "BASIC":
        session.auth = (S4_BASIC_USER, S4_BASIC_PASS)

    # Build $filter with optional clauses
    filters = []

    if sales_org:
        filters.append(f"SalesOrganization eq '{quote(sales_org)}'")
    if sold_to:
        filters.append(f"SoldToParty eq '{quote(sold_to)}'")
    if distribution_channel:
        filters.append(f"DistributionChannel eq '{quote(distribution_channel)}'")
    if division:
        filters.append(f"OrganizationDivision eq '{quote(division)}'")

    if since_iso and S4_SO_CHANGED_FIELD:
        # Only add if property exists; otherwise skip
        if _entity_has_property(session, S4_BASE_URL, "A_SalesOrder", S4_SO_CHANGED_FIELD, headers):
            filters.append(_since_literal(S4_SO_CHANGED_FIELD, since_iso))
        else:
            log.warning("Property %s not found on A_SalesOrder; skipping 'since' filter.", S4_SO_CHANGED_FIELD)

    filt = " and ".join(filters) if filters else None

    select_fields = [
        "SalesOrder",
        "SalesOrderType",
        "SalesOrganization",
        "DistributionChannel",
        "OrganizationDivision",
        "SoldToParty",
        "CreationDate",
        "LastChangeDateTime",
        "OverallSDProcessStatus",
    ]

    params = {
        "$select": ",".join(select_fields),
        "$orderby": order_by,
        "$top": str(max(1, min(max_results, 1000))),  # guardrails
    }
    if filt:
        params["$filter"] = filt

    url = f"{entity}?{urlencode(params)}"
    log.debug("List Sales Orders (with since?): %s", url)

    try:
        rows = _odata_get_all(session, url, headers)
    except requests.HTTPError as e:
        if since_iso and e.response is not None and e.response.status_code == 400:
            log.warning("400 with %s filter on A_SalesOrder; retrying without 'since'", S4_SO_CHANGED_FIELD or "<unset>")
            url2 = _strip_since_filter(url, S4_SO_CHANGED_FIELD)
            log.debug("List Sales Orders (no since): %s", url2)
            rows = _odata_get_all(session, url2, headers)
        else:
            raise

    # Map to clean structure
    out = []
    for r in rows:
        out.append({
            "salesOrder": r.get("SalesOrder"),
            "salesOrderType": r.get("SalesOrderType"),
            "salesOrganization": r.get("SalesOrganization"),
            "distributionChannel": r.get("DistributionChannel"),
            "division": r.get("OrganizationDivision"),
            "soldToParty": r.get("SoldToParty"),
            "creationDate": r.get("CreationDate"),
            "lastChangeDateTime": r.get("LastChangeDateTime"),
            "overallSDProcessStatus": r.get("OverallSDProcessStatus"),
        })
    return out

def convert_to_datetime(date_string):
    # Use a regular expression to extract the numeric part
    match = re.search(r'\d+', date_string)
    if match:
        # Get the number and convert it to an integer
        timestamp_ms = int(match.group(0))

        # The timestamp is in milliseconds, so divide by 1000 to get seconds
        timestamp_s = timestamp_ms / 1000

        # Convert the Unix timestamp to a datetime object
        normal_date = datetime.datetime.fromtimestamp(timestamp_s)

        # Print the result
        return normal_date.strftime("%Y-%m-%d")

    else:
        return None

def return_output(order_ids, since_iso = None, fresh_atp = None):
    output = get_promise_rate(order_ids, since_iso=since_iso, fresh_atp=fresh_atp)
    for order in output['orders']:
        for item in order['items']:
            confirmed_delivery_date = item.get('confirmedDeliveryDate')
                
            if confirmed_delivery_date:
                # Convert to datetime and append to the list
                new_confirmed_date = convert_to_datetime(confirmed_delivery_date)
                item['confirmedDeliveryDate'] = new_confirmed_date
                
            requested_delivery_date = item.get('requestedDeliveryDate')
                
            if requested_delivery_date:
                    # Convert to datetime and append to the list
                new_requested_delivery_date = convert_to_datetime(requested_delivery_date)
                item['requestedDeliveryDate'] = new_requested_delivery_date
        #print(output['orders'])['items']
    return json.dumps(output, indent=2, ensure_ascii=False)

def update_orders(order_ids):
    output = return_output(order_ids)
    output1 = []
    output = json.loads(output)
    for order in output['orders']:
        order_id = order.get('orderId')
        orderedQty = order.get('orderedQty')
        confirmedQty = order.get('confirmedQty')
        promise_rate = order.get('orderPromiseRate')
       
        if len([datetime.datetime.strptime(str(item.get('requestedDeliveryDate')), "%Y-%m-%d") for item in order['items'] if item.get('requestedDeliveryDate') is not None ])>=1:
            requestedDeliveryDate = min([datetime.datetime.strptime(str(item.get('requestedDeliveryDate')), "%Y-%m-%d") for item in order['items'] if item.get('requestedDeliveryDate') is not None ]).date().strftime("%Y-%m-%d")
        else:
            requestedDeliveryDate = ""
        if len([datetime.datetime.strptime(str(item.get('confirmedDeliveryDate')), "%Y-%m-%d") for item in order['items'] if item.get('confirmedDeliveryDate') is not None ])>=1:
            confirmedDeliveryDate = min([datetime.datetime.strptime(str(item.get('confirmedDeliveryDate')), "%Y-%m-%d") for item in order['items'] if item.get('confirmedDeliveryDate') is not None ]).date().strftime("%Y-%m-%d")
        else:
            confirmedDeliveryDate = ""
        output1.append([
                int(order_id),
                orderedQty,
                confirmedQty,
                promise_rate,
                requestedDeliveryDate,
                confirmedDeliveryDate
                ])
    df = pd.DataFrame(output1, columns=['order_id', 'orderedQty', 'confirmedQty','promise_rate','requestedDeliveryDate','confirmedDeliveryDate'])
    default_orders_path = "data/sample_orders.csv"
    #orders_df = pd.read_csv(default_orders_path)
    #merged_df = orders_df.merge(df,how="left")
    return df

    # if date_string:
    #     # Extract the timestamp using regex
    #     match = re.search(r'/Date\\((\d+)\\)/', date_string)
    #     if match:
    #         timestamp = int(match.group(1)) // 1000  # Convert milliseconds to seconds
    #         return datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    # return None



# # ---------------------------
# # CLI demo
# # ---------------------------

# if __name__ == "__main__":
#     import argparse
    
#     # parser = argparse.A:rgumentParser(description="Compute item-weighted promise rate for S/4 sales orders.")
#     # parser.add_argument("--orders", required=True, help="Comma-separated SalesOrder IDs, e.g. 50012345,50012346")
#     # parser.add_argument("--since", default=None, help="ISO timestamp for 'recently promised' filter (e.g., 2025-09-07T00:00:00Z)")
#     # parser.add_argument("--fresh-atp", action="store_true", help="Re-check ATP via BAPI_MATERIAL_AVAILABILITY using PyRFC")
#     # args = parser.parse_args()

#     # order_ids = [x.strip() for x in args.orders.split(",") if x.strip()]
    
#     # output = get_promise_rate(order_ids, since_iso=args.since, fresh_atp=args.fresh_atp)
#     # print(json.dumps(output, indent=2, ensure_ascii=False))
    
#     #get sales orders
#     parser = argparse.ArgumentParser(description="S/4: Promise Rate + Sales Order listing.")
#     sub = parser.add_mutually_exclusive_group()

#     # # default mode (promise rate)
#     parser.add_argument("--orders", help="Comma-separated SalesOrder IDs, e.g. 50012345,50012346")
#     parser.add_argument("--since", default=None, help="ISO timestamp for 'recently promised' filter (e.g., 2025-09-07T00:00:00Z)")
#     parser.add_argument("--fresh-atp", action="store_true", help="Re-check ATP via BAPI_MATERIAL_AVAILABILITY using PyRFC")

#     # NEW list mode
#     sub.add_argument("--list-orders", action="store_true", help="List Sales Orders from A_SalesOrder")
#     parser.add_argument("--max", type=int, default=100, help="Max results for --list-orders (default 100)")
#     parser.add_argument("--sales-org", default=None, help="Filter: SalesOrganization")
#     parser.add_argument("--sold-to", default=None, help="Filter: SoldToParty")
#     parser.add_argument("--dist-channel", default=None, help="Filter: DistributionChannel")
#     parser.add_argument("--division", default=None, help="Filter: OrganizationDivision")
#     parser.add_argument("--orderby", default="SalesOrder desc", help="Order by for --list-orders (default 'SalesOrder desc')")

#     args = parser.parse_args()

#     if args.list_orders:
#         result = list_sales_orders(
#             since_iso=args.since,
#             sales_org=args.sales_org,
#             sold_to=args.sold_to,
#             distribution_channel=args.dist_channel,
#             division=args.division,
#             max_results=args.max,
#             order_by=args.orderby,
#         )
#         print(json.dumps(result, indent=2, ensure_ascii=False))
#     else:
#         if not args.orders:
#             parser.error("--orders is required unless you use --list-orders")
        
#         order_ids = [x.strip() for x in args.orders.split(",") if x.strip()]
#         print(order_ids)
        
#         output = update_orders(order_ids)
#         print(output)
        
       