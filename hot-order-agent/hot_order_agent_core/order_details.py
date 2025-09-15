#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pull order snapshot from S/4HANA:
(order_id, product_id/name, order_qty, customerid, priority, origin_city, destination_city, customer_email)

APIs:
- API_SALES_ORDER_SRV (OData V2)
- API_BUSINESS_PARTNER (OData V2)
- API_PRODUCT_SRV (OData V2)
- API_Plant_2 (OData V4)
"""

import os
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()



REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SEC", "60"))
VERIFY = os.getenv("REQUESTS_VERIFY", "true").lower() != "false"

# ---- Base URLs (service roots; no trailing slash) ----
S4_SALES_BASE   = os.getenv("S4_SALES_BASE_URL", "").rstrip("/")  # e.g. https://<host>/sap/opu/odata/sap/API_SALES_ORDER_SRV
S4_BP_BASE      = os.getenv("S4_BP_BASE_URL", "").rstrip("/")      # e.g. https://<host>/sap/opu/odata/sap/API_BUSINESS_PARTNER
S4_PRODUCT_BASE = os.getenv("S4_PRODUCT_BASE_URL", "").rstrip("/") # e.g. https://<host>/sap/opu/odata/sap/API_PRODUCT_SRV
S4_PLANT_BASE   = os.getenv("S4_PLANT_BASE_URL", "").rstrip("/")   # e.g. https://<host>/sap/opu/odata4/sap/api_plant_2/srvd_a2x/sap/plant/0001

# ---- Auth (Basic or OAuth Client Credentials) ----
AUTH_MODE = os.getenv("S4_AUTH_MODE", "BASIC").upper()
BASIC_USER = os.getenv("S4_BASIC_USER")
BASIC_PASS = os.getenv("S4_BASIC_PASS")
OAUTH_TOKEN_URL = os.getenv("S4_OAUTH_TOKEN_URL")
OAUTH_CLIENT_ID = os.getenv("S4_OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.getenv("S4_OAUTH_CLIENT_SECRET")
OAUTH_SCOPE = os.getenv("S4_OAUTH_SCOPE", "")

LANG = os.getenv("LANGUAGE", "EN")  # for product description

# ---------------- Session & helpers ----------------


import re
import requests

def _session_with_auth():
    # reuse your existing _session() or use this if you don’t have one yet
    s = requests.Session()
    if AUTH_MODE == "BASIC":
        s.auth = (BASIC_USER, BASIC_PASS)
    elif AUTH_MODE == "OAUTH":
        tok = requests.post(
            OAUTH_TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
            auth=(OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT, verify=VERIFY,
        )
        tok.raise_for_status()
        s.headers["Authorization"] = f"Bearer {tok.json()['access_token']}"
    s.headers["Accept"] = "application/json"
    return s

def _get_metadata_text(session: requests.Session, base_url: str) -> str | None:
    try:
        r = session.get(f"{base_url}/$metadata", timeout=REQUEST_TIMEOUT, verify=VERIFY)
        if r.status_code == 200:
            return r.text
        return None
    except Exception:
        return None
    

def _resolve_sales_service_base() -> str:
    """
    Try unversioned, then ;v=0002, then ;v=0001 until we find a base that returns $metadata.
    """
    if not S4_SALES_BASE:
        raise RuntimeError("S4_SALES_BASE_URL not set")
    bases = [S4_SALES_BASE.rstrip("/")]
    if ";v=" not in bases[0]:
        bases += [bases[0] + ";v=0002", bases[0] + ";v=0001"]

    s = _session_with_auth()
    for b in bases:
        meta = _get_metadata_text(s, b)
        if meta:
            # found a working base
            return b
    # If none returned $metadata, keep original so you see a clear error
    return bases[0]

def _resolve_item_entity_set(session: requests.Session, base_url: str) -> str:
    """
    From $metadata, pick the entity set for sales order items.
    Prefer 'A_SalesOrderItem', else pick any *SalesOrder*Item* set.
    """
    meta = _get_metadata_text(session, base_url) or ""
    # Collect all entity set names
    names = re.findall(r'EntitySet Name="([^"]+)"', meta)
    # Exact preferred name
    if "A_SalesOrderItem" in names:
        return "A_SalesOrderItem"
    # Fallback: best effort match
    for n in names:
        if "SalesOrder" in n and "Item" in n:
            return n
    # Last resort: the usual name
    return "A_SalesOrderItem"

def _session():
    s = requests.Session()
    if AUTH_MODE == "BASIC":
        s.auth = (BASIC_USER, BASIC_PASS)
    elif AUTH_MODE == "OAUTH":
        tok = requests.post(
            OAUTH_TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
            auth=(OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT, verify=VERIFY,
        )
        tok.raise_for_status()
        s.headers["Authorization"] = f"Bearer {tok.json()['access_token']}"
    s.headers["Accept"] = "application/json"
    return s

def _odata_get_all_v2(session: requests.Session, url: str):
    out = []
    while True:
        r = session.get(url, timeout=REQUEST_TIMEOUT, verify=VERIFY)
        r.raise_for_status()
        j = r.json()
        d = j.get("d", {})
        out.extend(d.get("results", []))
        next_url = d.get("__next")
        if not next_url:
            break
        url = next_url
    return out

# ---------------- Fetchers ----------------

def fetch_order_items(order_ids):
    """Read items with product, qty, priority, plant + header SoldTo/ShipTo in one call."""
    if not S4_SALES_BASE:
        raise RuntimeError("S4_SALES_BASE_URL not set")

    # 1) Find a working service root (handles ;v=0002)
    sales_base = _resolve_sales_service_base()

    # 2) Resolve the correct entity set name from $metadata
    sess = _session_with_auth()
    item_set = _resolve_item_entity_set(sess, sales_base)

    #or_terms = [f"SalesOrder eq '{oid}'" for oid in order_ids]
    or_terms = [f"SalesOrder eq '{oid}'" for oid in order_ids]
    expand = "to_SalesOrder"
    select = ",".join([
        "SalesOrder","SalesOrderItem",
        "Material",
        "RequestedQuantity","RequestedQuantityUnit",
        "DeliveryPriority",
        "ProductionPlant",
        "to_SalesOrder/SoldToParty",
        "to_SalesOrder/ShipToParty",
        

    ])
    
    #expand = "to_SalesOrder($select=SoldToParty,ShipToParty)"
    # params = {"$filter": "(" + " or ".join(or_terms) + ")", "$select": select}
    # #params = {"$filter": "(" + " or ".join(or_terms) + ")", "$select": select, "$expand": expand}
    # #url= f"{sales_base}/{item_set}"
    # url = f"{sales_base}/{item_set}?{urlencode(params)}"
    params = {
    "$filter": "(" + " or ".join([f"SalesOrder eq '{oid}'" for oid in order_ids]) + ")",
    "$expand": expand,
    }
    url = f"{sales_base}/{item_set}?{urlencode(params)}"
    rows = _odata_get_all_v2(sess, url)
    
    # Optional: log the final URL
    #print(f"[DEBUG] Sales item URL: {url}")
    #print(_odata_get_all_v2(sess, url))
    return _odata_get_all_v2(sess, url)


# def fetch_order_items(order_ids):
#     """A_SalesOrderItem with header nav for SoldToParty; gives product id, qty, priority, plant."""
    
#     if not S4_SALES_BASE:
#         raise RuntimeError("S4_SALES_BASE_URL not set")
#     entity = f"{S4_SALES_BASE}/A_SalesOrderItem"
#     or_terms = [f"SalesOrder eq '{oid}'" for oid in order_ids]
#     select = ",".join([
#         "SalesOrder","SalesOrderItem",
#         "Material","RequestedQuantity","RequestedQuantityUnit",
#         "DeliveryPriority","ProductionPlant"
#     ])
#     expand = "to_SalesOrder($select=SoldToParty,ShipToParty)"
#     params = {"$filter": "(" + " or ".join(or_terms) + ")", "$select": select, "$expand": expand}
#     url = f"{entity}?{urlencode(params)}"
#     return _odata_get_all_v2(_session(), url)

def fetch_destination_cities(order_ids):
    """A_SalesOrderPartnerAddress for Ship-to partner document address → CityName."""
    if not S4_SALES_BASE:
        raise RuntimeError("S4_SALES_BASE_URL not set")
    entity = f"{S4_SALES_BASE}/A_SalesOrderPartnerAddress"
    or_terms = [f"(SalesOrder eq '{oid}' and PartnerFunction eq 'SH')" for oid in order_ids]
    params = {"$filter": "(" + " or ".join(or_terms) + ")"}
    #params = {"$filter": "(" + " or ".join(or_terms) + ")", "$select": "SalesOrder,CityName"}
    url = f"{entity}?{urlencode(params)}"
    rows = _odata_get_all_v2(_session(), url)
    print(rows)
    return {r["SalesOrder"]: r.get("CityName") for r in rows}

def fetch_product_names(material_ids):
    """API_PRODUCT_SRV: A_ProductDescription (fallback to A_ProductText) → product name per language."""
    if not material_ids:
        return {}
    if not S4_PRODUCT_BASE:
        # only product ids will be returned
        return {}
    s = _session()
    names = {}
    # Fetch one-by-one to keep filters simple/robust in on-prem gateways
    for mat in sorted(set(material_ids)):
        # Try A_ProductDescription first
        try:
            url = (f"{S4_PRODUCT_BASE}/A_ProductDescription?"
                   f"$filter=Product eq '{mat}' and Language eq '{LANG}'"
                   f"&$select=Product,ProductDescription")
            r = s.get(url, timeout=REQUEST_TIMEOUT, verify=VERIFY); r.raise_for_status()
            vals = r.json().get("d", {}).get("results", [])
            if vals:
                names[mat] = vals[0].get("ProductDescription")
                continue
        except Exception:
            pass
        # Fallback: A_ProductText
        try:
            url = (f"{S4_PRODUCT_BASE}/A_ProductText?"
                   f"$filter=Product eq '{mat}' and Language eq '{LANG}'"
                   f"&$select=Product,ProductDescription")
            r = s.get(url, timeout=REQUEST_TIMEOUT, verify=VERIFY); r.raise_for_status()
            vals = r.json().get("d", {}).get("results", [])
            if vals:
                names[mat] = vals[0].get("ProductDescription")
        except Exception:
            names.setdefault(mat, None)
    return names

def fetch_origin_cities(plants):
    """API_Plant_2 (OData V4) → Plant.CityName. If not available, return {} and we’ll use the plant code."""
    if not plants:
        return {}
    if not S4_PLANT_BASE:
        return {}
    s = _session()
    out = {}
    for p in sorted(set(plants)):
        url = f"{S4_PLANT_BASE}/Plant?$filter=Plant eq '{p}'&$select=Plant,CityName"
        try:
            r = s.get(url, timeout=REQUEST_TIMEOUT, verify=VERIFY); r.raise_for_status()
            vals = r.json().get("value", [])
            if vals:
                out[p] = vals[0].get("CityName")
        except Exception:
            out.setdefault(p, None)
    return out

def fetch_bp_emails(bp_ids):
    """API_BUSINESS_PARTNER → default email from addresses; fallback to first email."""
    if not bp_ids:
        return {}
    if not S4_BP_BASE:
        raise RuntimeError("S4_BP_BASE_URL not set")
    s = _session()
    out = {}
    for bp in sorted(set(bp_ids)):
        url = (f"{S4_BP_BASE}/A_BusinessPartner('{bp}')"
               f"?$select=BusinessPartner"
               f"&$expand=to_BusinessPartnerAddress($expand=to_EmailAddress)")
        try:
            j = s.get(url, timeout=REQUEST_TIMEOUT, verify=VERIFY).json()
            addrs = j.get("d", {}).get("to_BusinessPartnerAddress", {}).get("results", []) or []
            email = None
            for a in addrs:
                emails = a.get("to_EmailAddress", {}).get("results", []) or []
                # prefer default
                for e in emails:
                    if e.get("IsDefaultEmailAddress") is True:
                        email = e.get("EmailAddress"); break
                if email: break
                if emails and not email:
                    email = emails[0].get("EmailAddress")
            out[bp] = email
        except Exception:
            out[bp] = None
    return out

# ---------------- Orchestrator ----------------

def get_orders_snapshot(order_ids):
    items = fetch_order_items(order_ids)

    # lookups
    materials = {it.get("Material") for it in items if it.get("Material")}
    plants = {it.get("ProductionPlant") for it in items if it.get("ProductionPlant")}
    sold_tos = { (it.get("to_SalesOrder") or {}).get("SoldToParty")
                 for it in items if it.get("to_SalesOrder") }
    sold_tos.discard(None)

    prod_names = fetch_product_names(materials)
    origin_cities = fetch_origin_cities(plants)
    dest_cities = fetch_destination_cities(order_ids)
    emails = fetch_bp_emails(sold_tos)

    rows = []
    for it in items:
        so = it.get("SalesOrder")
        mat = it.get("Material")
        plant = it.get("ProductionPlant")
        sold_to = (it.get("to_SalesOrder") or {}).get("SoldToParty")
        rows.append({
            "order_id": so,
            "product_id": mat,
            "product_name": prod_names.get(mat),
            "order_qty": it.get("RequestedQuantity"),
            "customerid": sold_to,
            "priority": it.get("DeliveryPriority"),
            "origin_city": origin_cities.get(plant),
            "destination_city": dest_cities.get(so),
            "customer_email": emails.get(sold_to),
        })
    return rows

# ---------------- CLI demo ----------------

if __name__ == "__main__":
    # Example: set env vars, then:


    if __name__ == "__main__":
    # Quick probe: verify service/metadata and entity set
        sbase = _resolve_sales_service_base()
        sess = _session_with_auth()
        es = _resolve_item_entity_set(sess, sbase)
        print(f"[INFO] Using sales service base: {sbase}")
        print(f"[INFO] Item entity set: {es}")
        # Now call the normal flow
        order_ids = [s.strip() for s in os.getenv("ORDER_IDS","1000088,1000001").split(",") if s.strip()]
        data = get_orders_snapshot(order_ids)
        from pprint import pprint; pprint(data)

    # #  set S4_SALES_BASE_URL=https://<host>/sap/opu/odata/sap/API_SALES_ORDER_SRV
    # #  set S4_BP_BASE_URL=https://<host>/sap/opu/odata/sap/API_BUSINESS_PARTNER
    # #  set S4_PRODUCT_BASE_URL=https://<host>/sap/opu/odata/sap/API_PRODUCT_SRV
    # #  set S4_PLANT_BASE_URL=https://<host>/sap/opu/odata4/sap/api_plant_2/srvd_a2x/sap/plant/0001
    # #  set S4_AUTH_MODE=BASIC & set S4_BASIC_USER=... & set S4_BASIC_PASS=...
    # order_ids = [s.strip() for s in os.getenv("ORDER_IDS", "1000088,10").split(",") if s.strip()]
    # data = get_orders_snapshot(order_ids)
    # from pprint import pprint
    # pprint(data)
