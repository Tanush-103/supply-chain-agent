#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, math
import requests
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()


REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SEC", "60"))
VERIFY = os.getenv("REQUESTS_VERIFY", "true").lower() != "false"

# ---- Base URL for the Material Stock API (no trailing slash) ----
# e.g. https://<host>/sap/opu/odata/sap/API_MATERIAL_STOCK_SRV
S4_STOCK_BASE = os.getenv("S4_STOCK_BASE_URL", "").rstrip("/")

# ---- Auth (BASIC or OAUTH) ----
AUTH_MODE = os.getenv("S4_AUTH_MODE", "BASIC").upper()
BASIC_USER = os.getenv("S4_BASIC_USER")
BASIC_PASS = os.getenv("S4_BASIC_PASS")
OAUTH_TOKEN_URL = os.getenv("S4_OAUTH_TOKEN_URL")
OAUTH_CLIENT_ID = os.getenv("S4_OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.getenv("S4_OAUTH_CLIENT_SECRET")
OAUTH_SCOPE = os.getenv("S4_OAUTH_SCOPE", "")

# Stock type '01' = Unrestricted-Use (per SAP doc)
UNRESTRICTED_STOCK_TYPE = "01"

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

def _get_metadata(session: requests.Session, base: str) -> str | None:
    try:
        r = session.get(f"{base}/$metadata", timeout=REQUEST_TIMEOUT, verify=VERIFY)
        return r.text if r.ok else None
    except Exception:
        return None

def _resolve_stock_service_base() -> str:
    """
    Try unversioned and common versioned roots (e.g., ;v=0002) until $metadata responds.
    """
    if not S4_STOCK_BASE:
        raise RuntimeError("S4_STOCK_BASE_URL not set")
    bases = [S4_STOCK_BASE]
    if ";v=" not in S4_STOCK_BASE:
        bases += [S4_STOCK_BASE + ";v=0002", S4_STOCK_BASE + ";v=0001"]
    s = _session()
    for b in bases:
        if _get_metadata(s, b):
            return b
    return S4_STOCK_BASE  # fall back to original; errors will be explicit

def _resolve_stock_entity_set(session: requests.Session, base_url: str) -> str:
    """
    Prefer A_MatlStkInAcctMod; fall back to any EntitySet containing 'Matl'+'Stk' or 'MaterialStock'.
    """
    meta = _get_metadata(session, base_url) or ""
    names = re.findall(r'EntitySet Name="([^"]+)"', meta)
    if "A_MatlStkInAcctMod" in names:
        return "A_MatlStkInAcctMod"
    for n in names:
        if ("Matl" in n and "Stk" in n) or ("Material" in n and "Stock" in n):
            return n
    return "A_MatlStkInAcctMod"

def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

def fetch_available_onhand(dc_list, sku_list, group_by_sloc=False):
    """
    Returns list of dicts:
      { 'dc': <Plant>, 'sku': <Material>, 'available_qty': <float>, [ 'storage_location': <str> ] }
    """
    base = _resolve_stock_service_base()
    sess = _session()
    entity = _resolve_stock_entity_set(sess, base)

    # Build (Material, Plant) filters. Gateway URLs can get long → chunk.
    pairs = [(m, p) for p in dc_list for m in sku_list]
    if not pairs:
        return []

    results = []
    select = ",".join([
        "Material", "Plant",
        "StorageLocation",
        "MaterialBaseUnit",
        "MatlWrhsStkQtyInMatlBaseUnit",
        "InventoryStockType"
    ])

    # 40–60 conditions per call is usually safe; tune for your gateway/proxy.
    for batch in _chunks(pairs, 50):
        ors = [f"(Material eq '{m}' and Plant eq '{p}' and InventoryStockType eq '{UNRESTRICTED_STOCK_TYPE}')"
               for (m, p) in batch]
        params = {
            "$filter": "(" + " or ".join(ors) + ")",
            "$select": select
        }
        url = f"{base}/{entity}?{urlencode(params)}"
        rows = _odata_get_all_v2(sess, url)
        for r in rows:
            if r.get("InventoryStockType") != UNRESTRICTED_STOCK_TYPE:
                continue
            results.append({
                "dc": r.get("Plant"),
                "sku": r.get("Material"),
                "storage_location": r.get("StorageLocation"),
                "base_unit": r.get("MaterialBaseUnit"),
                "qty_base_uom": float(r.get("MatlWrhsStkQtyInMatlBaseUnit") or 0.0)
            })

    # Aggregate
    from collections import defaultdict
    agg = defaultdict(float)
    if group_by_sloc:
        for x in results:
            key = (x["dc"], x["sku"], x["storage_location"])
            agg[key] += x["qty_base_uom"]
        return [
            {"dc": k[0], "sku": k[1], "storage_location": k[2], "available_qty": v}
            for k, v in agg.items()
        ]
    else:
        for x in results:
            key = (x["dc"], x["sku"])
            agg[key] += x["qty_base_uom"]
        return [
            {"dc": k[0], "sku": k[1], "available_qty": v}
            for k, v in agg.items()
        ]

# --------------- CLI demo ---------------
if __name__ == "__main__":
    # Env you need (examples):
    #   set S4_STOCK_BASE_URL=https://<host>/sap/opu/odata/sap/API_MATERIAL_STOCK_SRV
    #   set S4_AUTH_MODE=BASIC
    #   set S4_BASIC_USER=...
    #   set S4_BASIC_PASS=...
    DC_LIST = [s.strip() for s in os.getenv("PLANTS", "1710,1100").split(",") if s.strip()]
    SKU_LIST = [s.strip() for s in os.getenv("SKUS", "FG-01,FG-002").split(",") if s.strip()]
    data = fetch_available_onhand(DC_LIST, SKU_LIST, group_by_sloc=False)
    from pprint import pprint; pprint(data)
