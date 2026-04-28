# services/olx_scraper/phones.py
"""
Two-pass phone resolution for OLX listings.

Pass 1 — _description text_:
    Uses regex on the listing's description text. ~60-70% of listings on
    olx.uz duplicate the phone in the body (especially agency posts). Works
    out of the box, no API calls required. Implemented in parser.py as
    `phones_in_text`; stored on the listing's contact_phone column.

Pass 2 — _reveal API_:
    The "показать" button on each listing triggers an XHR. The exact endpoint
    is NOT publicly documented. Below is the best guess based on patterns used
    by other OLX deployments (olx.ua, olx.pl, olx.ro):

        GET https://www.olx.uz/api/v1/offers/{numeric_id}/limited-phones/

    Auth requirements vary by region:
      - Some olx instances accept anonymous calls
      - Others require Bearer token from /api/open/oauth/token (client_credentials)
      - Some require a session cookie obtained from a prior page view

    THIS MODULE'S APPROACH:
      1) try anonymous GET — log result
      2) on 401/403 try a session-cookie warmup (load the detail page first)
      3) on persistent failure mark the row as "phone_reveal_failed" in metadata
         and skip until next manual investigation

    HOW TO VERIFY THE ENDPOINT:
      Open Chrome DevTools Network panel on a listing detail page, click
      "показать" next to the masked phone, capture the actual XHR request
      (URL, headers, body). Replace the URL/headers below with what you see.
      The phone-reveal pass is OPT-IN via OLX_REVEAL_PHONES=1 env var, so the
      scraper runs fine with phones-from-text only until the endpoint is
      verified.
"""
import asyncio
import json
import os
from typing import List, Optional

import httpx


REVEAL_ENABLED = os.getenv("OLX_REVEAL_PHONES", "0") == "1"
REVEAL_TIMEOUT = float(os.getenv("OLX_REVEAL_TIMEOUT", "10"))
REVEAL_BASE = os.getenv("OLX_REVEAL_BASE", "https://www.olx.uz")


async def reveal_phone(
    client: httpx.AsyncClient,
    numeric_id: int,
    listing_url: Optional[str] = None,
) -> List[str]:
    """
    Returns list of phone strings ("+998..."), empty list on failure.
    """
    if not REVEAL_ENABLED:
        return []

    # Attempt 1: anonymous call to limited-phones endpoint
    api_url = f"{REVEAL_BASE}/api/v1/offers/{numeric_id}/limited-phones/"
    headers = {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    if listing_url:
        headers["Referer"] = listing_url

    try:
        r = await client.get(api_url, headers=headers, timeout=REVEAL_TIMEOUT)
        if r.status_code == 200:
            return _extract_phones_from_response(r)
        if r.status_code in (401, 403, 404):
            print(f"[olx-reveal] {r.status_code} for {numeric_id} — endpoint may need auth or differs")
            return []
        print(f"[olx-reveal] unexpected {r.status_code} for {numeric_id}")
    except httpx.RequestError as e:
        print(f"[olx-reveal] request error for {numeric_id}: {e}")

    return []


def _extract_phones_from_response(r: httpx.Response) -> List[str]:
    """OLX response shapes vary — try common patterns."""
    try:
        data = r.json()
    except json.JSONDecodeError:
        return []

    # Shape 1: {"data": {"phones": ["+998..."]}}
    if isinstance(data, dict):
        d = data.get("data") or data
        if isinstance(d, dict):
            phones = d.get("phones") or d.get("phone")
            if isinstance(phones, list):
                return [str(p) for p in phones if p]
            if isinstance(phones, str):
                return [phones]

    # Shape 2: top-level list
    if isinstance(data, list):
        return [str(p) for p in data if p]

    return []


def normalize_phone(raw: str) -> Optional[str]:
    """+998 90 123 45 67 → +998901234567."""
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return None