#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, datetime
import requests
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SEC", "60"))
VERIFY = os.getenv("REQUESTS_VERIFY", "true").lower() != "false"

# ---- Service root (no trailing slash) ----
# e.g. https://<host>/sap/opu/odata/sap/API_SLSPRICINGCONDITIONRECORD_SRV
S4_PRICING_BASE = os.getenv("S4_PRICING_BASE_URL", "").rstrip("/")

# ---- Auth (Basic or OAuth2 Client Credentials) ----
AUTH_MODE = os.getenv("S4_AUTH_MODE", "BASIC").upper()
BASIC_USER = os.getenv("S4_BASIC_USER"); BASIC_PASS = os.getenv("S4_BASIC_PASS")
OAUTH_TOKEN_URL = os.getenv("S4_OAUTH_TOKEN_URL")
OAUTH_CLIENT_ID = os.getenv("S4_OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.getenv("S4_OAUTH_CLIENT_SECRET")
OAUTH_SCOPE = os.getenv("S4_OAUTH_SCOPE", "")

# ---- Your condition types (override via env if different) ----
CT_BASE_RATE   = os.getenv("CT_BASE_RATE",   "ZSHP_BASE")
CT_EXP_MULT    = os.getenv("CT_EXP_MULT",    "ZSHP_EXP_MULT")
CT_BASE_DAYS   = os.getenv("CT_BASE_DAYS",   "ZSHP_BASE_DAYS")
CT_EXP_DAYS    = os.getenv("CT_EXP_DAYS",    "ZSHP_EXP_DAYS")

# Which key field in the condition table represents the DC?
# We'll try these in order (first one that exists in the validity payload wins).
DC_FIELD_CANDIDATES = [f.strip() for f in os.getenv("DC_FIELD_CANDIDATES", "Plant,ShippingPoint").split(",")]

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
    rows = []
    while True:
        r = session.get(url, timeout=REQUEST_TIMEOUT, verify=VERIFY)
        r.raise_for_status()
        d = r.json().get("d", {})
        rows.extend(d.get("results", []))
        url = d.get("__next")
        if not url:
            break
    return rows

def _get_metadata(session: requests.Session, base: str) -> str | None:
    try:
        r = session.get(f"{base}/$metadata", timeout=REQUEST_TIMEOUT, verify=VERIFY)
        return r.text if r.ok else None
    except Exception:
        return None

def _resolve_pricing_service_base() -> str:
    if not S4_PRICING_BASE:
        raise RuntimeError("S4_PRICING_BASE_URL not set")
    bases = [S4_PRICING_BASE]
    if ";v=" not in S4_PRICING_BASE:
        bases += [S4_PRICING_BASE + ";v=0002", S4_PRICING_BASE + ";v=0001"]
    s = _session()
    for b in bases:
        if _get_metadata(s, b):
            return b
    return S4_PRICING_BASE

def _today_literal():
    # OData V2 Edm.DateTime literal (no offset)
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return f"datetime'{today.isoformat()}'"

def _pick_dc_field(sample_row: dict) -> str | None:
    for k in DC_FIELD_CANDIDATES:
        if k in sample_row:
            return k
    return None

def _fetch_validities(base: str, cond_type: str):
    sess = _session()
    entity = f"{base}/A_SlsPrcgCndnRecdValidity"
    filt = (
        f"ConditionType eq '{cond_type}' and "
        f"ConditionValidityStartDate le {_today_literal()} and "
        f"ConditionValidityEndDate ge {_today_literal()}"
    )
    select = ",".join([
        "ConditionRecord","ConditionType",
        # include likely DC fields if present; gateway will ignore unknown selects
        "Plant","ShippingPoint","SalesOrganization","DistributionChannel","Customer","Material"
    ])
    url = f"{entity}?{urlencode({'$filter': filt, '$select': select})}"
    return _odata_get_all_v2(sess, url)

def _fetch_rate_row(base: str, condition_record: str):
    sess = _session()
    entity = f"{base}/A_SlsPrcgConditionRecord(ConditionRecord='{condition_record}')"
    params = {
        "$select": ",".join([
            "ConditionRecord","ConditionType",
            "ConditionRateValue","ConditionRateUnit","ConditionCurrency",
            "ConditionCalculationType"
        ])
    }
    url = f"{entity}?{urlencode(params)}"
    r = sess.get(url, timeout=REQUEST_TIMEOUT, verify=VERIFY)
    r.raise_for_status()
    return r.json().get("d", {})

def _first_rate_for_dc(dc: str, cond_type: str, base: str):
    # find validity rows for cond_type, pick the ones whose DC field matches dc, then get the rate row
    val_rows = _fetch_validities(base, cond_type)
    if not val_rows:
        return None

    dc_field = _pick_dc_field(val_rows[0])
    if not dc_field:
        # If your key doesn’t include Plant/ShippingPoint, remove DC filtering here or add your key field to DC_FIELD_CANDIDATES
        matches = val_rows
    else:
        matches = [v for v in val_rows if v.get(dc_field) == dc]

    for v in matches:
        rec_id = v.get("ConditionRecord")
        if not rec_id:
            continue
        try:
            rate = _fetch_rate_row(base, rec_id)
            return rate
        except requests.HTTPError:
            continue
    return None

def get_dc_shipping_params(dc_list):
    base = _resolve_pricing_service_base()
    out = []
    for dc in dc_list:
        # base rate
        base_rate_row = _first_rate_for_dc(dc, CT_BASE_RATE, base) or {}
        base_rate = base_rate_row.get("ConditionRateValue")
        # expedite multiplier
        exp_row = _first_rate_for_dc(dc, CT_EXP_MULT, base) or {}
        exp_val = exp_row.get("ConditionRateValue")
        calc_type = (exp_row.get("ConditionCalculationType") or "").upper()
        # assume % surcharge → multiplier = 1 + %/100; otherwise treat value as multiplier directly
        try:
            exp_val_num = float(exp_val) if exp_val is not None else None
        except Exception:
            exp_val_num = None
        if exp_val_num is None:
            exp_mult = None
        elif calc_type in ("B", "P", "PRCNT", "PERCENT"):  # different systems label this differently
            exp_mult = 1.0 + (exp_val_num / 100.0)
        else:
            exp_mult = exp_val_num

        # base/expedite days
        base_days_row = _first_rate_for_dc(dc, CT_BASE_DAYS, base) or {}
        exp_days_row  = _first_rate_for_dc(dc, CT_EXP_DAYS,  base) or {}
        def _to_float(x):
            try: return float(x) if x is not None else None
            except: return None

        out.append({
            "dc": dc,
            "base_rate_per_unit": _to_float(base_rate),
            "expedite_multiplier": exp_mult,
            "base_days": _to_float(base_days_row.get("ConditionRateValue")),
            "expedite_days": _to_float(exp_days_row.get("ConditionRateValue")),
            # optional metadata you might log or persist:
            # "currency": base_rate_row.get("ConditionCurrency"),
            # "rate_unit": base_rate_row.get("ConditionRateUnit"),
        })
    return out

if __name__ == "__main__":
    # Example:
    #   set S4_PRICING_BASE_URL=https://<host>/sap/opu/odata/sap/API_SLSPRICINGCONDITIONRECORD_SRV
    #   set S4_AUTH_MODE=BASIC & set S4_BASIC_USER=... & set S4_BASIC_PASS=...
    #   set CT_BASE_RATE=ZSHP_BASE & set CT_EXP_MULT=ZSHP_EXP_MULT & set CT_BASE_DAYS=ZSHP_BASE_DAYS & set CT_EXP_DAYS=ZSHP_EXP_DAYS
    dcs = [s.strip() for s in os.getenv("DCS", "1000,1100").split(",") if s.strip()]
    data = get_dc_shipping_params(dcs)
    from pprint import pprint; pprint(data)
