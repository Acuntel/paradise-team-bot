"""
Dashboard tracker client. Drop-in for Hermann + Paradise bots.

Two responsibilities:
  1. POST a row to email_sends in Supabase whenever the bot creates a
     draft. Returns the send_id (a UUID) so the rest of the pipeline
     can stamp tracking links into the email.
  2. Sign HMAC tokens that match the dashboard's lib/token.ts format,
     and produce {click, open, unsubscribe} URLs for a given send_id.

If the dashboard env vars aren't set, every function silently no-ops
so the bot keeps working without a dashboard. Zero overhead when off.

Env vars required (set on Railway / your local .env):
  DASHBOARD_BASE_URL                  e.g. https://paradise-dashboard.vercel.app
  TRACKER_HMAC_SECRET                 same secret as the dashboard
  DASHBOARD_SUPABASE_URL              same as the dashboard's SUPABASE_URL
  DASHBOARD_SUPABASE_SERVICE_KEY      same as the dashboard's SUPABASE_SERVICE_ROLE_KEY

Usage:
  from core import dashboard_tracker as dt
  send_id = dt.record_send(
      bot_name="brady",
      recipient="jane@example.com",
      subject="Working in Lake Forest this week",
      source_type="permit",
      tier="ultra",
      town="Lake Forest",
      channel="personal",
      subject_bucket="town_specific",
      credit_amount="$5,000",
  )
  click_url, open_pixel_url, unsub_url = dt.tracking_urls(
      send_id, real_cta_url="https://paradisehomedesigninc.com/gallery.html",
  )
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import uuid
from typing import Optional, Tuple
from urllib.parse import quote_plus

import requests

log = logging.getLogger(__name__)

_BASE = (os.getenv("DASHBOARD_BASE_URL") or "").rstrip("/")
_SUPA_URL = (os.getenv("DASHBOARD_SUPABASE_URL") or "").rstrip("/")
_SUPA_KEY = os.getenv("DASHBOARD_SUPABASE_SERVICE_KEY") or ""
_HMAC_SECRET_RAW = os.getenv("TRACKER_HMAC_SECRET") or ""
_HMAC_SECRET = _HMAC_SECRET_RAW.encode("utf-8")


def is_enabled() -> bool:
    return bool(_BASE and _SUPA_URL and _SUPA_KEY and _HMAC_SECRET_RAW)


def record_send(
    bot_name: str,
    recipient: str,
    subject: str,
    source_type: Optional[str] = None,
    tier: Optional[str] = None,
    town: Optional[str] = None,
    channel: Optional[str] = None,
    subject_bucket: Optional[str] = None,
    credit_amount: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> Optional[str]:
    """POST one row to email_sends. Returns send_id (UUID) on success,
    None on failure or when the dashboard isn't configured."""
    if not (_SUPA_URL and _SUPA_KEY):
        return None
    payload = {
        "bot_name": bot_name,
        "recipient_email": (recipient or "").lower(),
        "recipient_first_name": first_name,
        "recipient_last_name": last_name,
        "subject": subject,
        "subject_bucket": subject_bucket,
        "source_type": source_type,
        "home_value_tier": tier,
        "town": town,
        "channel": channel,
        "credit_amount": credit_amount,
    }
    try:
        r = requests.post(
            f"{_SUPA_URL}/rest/v1/email_sends",
            headers={
                "apikey": _SUPA_KEY,
                "Authorization": f"Bearer {_SUPA_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json=payload,
            timeout=8,
        )
        if r.ok:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0].get("id")
        else:
            log.warning("dashboard record_send %d: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("dashboard record_send failed (non-fatal): %s", e)
    return None


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def sign_token(send_id: str) -> Optional[str]:
    """HMAC-sign a send_id into the same token format the dashboard
    verifies in lib/token.ts. Returns None if HMAC secret missing."""
    if not _HMAC_SECRET:
        return None
    try:
        raw = uuid.UUID(send_id).bytes
    except Exception:
        return None
    sig = hmac.new(_HMAC_SECRET, raw, hashlib.sha256).digest()[:16]
    return f"{_b64url(raw)}.{_b64url(sig)}"


def tracking_urls(
    send_id: Optional[str],
    real_cta_url: str = "",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (click_url, open_pixel_url, unsubscribe_url).

    - click_url replaces real_cta_url in the email (logs click then
      302s to real_cta_url). Falls back to real_cta_url if disabled.
    - open_pixel_url is a 1x1 GIF endpoint to embed at end of <body>.
    - unsubscribe_url is for the footer link AND the List-Unsubscribe
      header.

    All three are None when the dashboard isn't configured. Caller
    should noop on None.
    """
    if not (send_id and _BASE):
        return real_cta_url, None, None
    tok = sign_token(send_id)
    if not tok:
        return real_cta_url, None, None
    click = f"{_BASE}/api/t/c/{tok}?u={quote_plus(real_cta_url)}" if real_cta_url else f"{_BASE}/api/t/c/{tok}"
    open_pixel = f"{_BASE}/api/t/o/{tok}.gif"
    unsub = f"{_BASE}/api/t/u/{tok}"
    return click, open_pixel, unsub


def list_unsubscribe_headers(unsub_url: Optional[str]) -> dict:
    """Return the List-Unsubscribe + List-Unsubscribe-Post headers to
    add to the outgoing message. Empty dict if no unsub URL.

    Mandatory for Gmail/Yahoo at any volume in 2026 -- including the
    headers measurably raises inbox placement.
    """
    if not unsub_url:
        return {}
    return {
        "List-Unsubscribe": f"<{unsub_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
