"""
Email verification with three layers (HighTicket pattern):
  1. MX records — domain can receive email
  2. Found-on-site — email literally appears on a scraped page or in the
     mailto: links the scraper extracted
  3. SMTP RCPT probe — receiving server explicitly accepts the address

Layer 3 is gated behind ENABLE_SMTP_PROBE because many ISPs block outbound
:25 and many big-mail servers reject all probes; MX + on-site is already
strong on its own.

Backwards-compatible exports:
  - verify(email, sender_address, strict_smtp=False) — legacy MX-only path
  - mx_ok(email) — used by stream_enrich and the cron
  - verify_email(email, page_text="", scraped_emails=None) — new 3-layer
"""
import logging
import os
import re
import smtplib
import socket

import dns.resolver

log = logging.getLogger(__name__)

EMAIL_FORMAT_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)
ENABLE_SMTP_PROBE = os.getenv("ENABLE_SMTP_PROBE", "false").lower() in (
    "true", "1", "yes",
)
PROBE_FROM = os.getenv("SMTP_PROBE_FROM", "verifier@paradisehomedesigninc.com")
PROBE_TIMEOUT = 8

PLACEHOLDER_EMAILS = {
    "example@domain.com", "name@example.com", "email@example.com",
    "you@example.com", "user@example.com", "test@test.com",
    "your@email.com", "info@example.com",
}


def looks_valid_format(email):
    return bool(EMAIL_FORMAT_RE.match((email or "").strip()))


def _mx_records(domain):
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        return sorted(answers, key=lambda r: r.preference)
    except Exception as e:
        log.debug("MX lookup failed for %s: %s", domain, e)
        return []


def mx_ok(email):
    if not email or "@" not in email:
        return False
    domain = email.split("@", 1)[1]
    return len(_mx_records(domain)) > 0


def found_on_scraped(email, page_text="", scraped_emails=None):
    """True if the email literally appeared on the scraped material —
    either in body text or among the mailto:/text-extracted email set."""
    e = (email or "").lower().strip()
    if not e:
        return False
    if scraped_emails:
        if any(e == s.lower() for s in scraped_emails):
            return True
    if page_text and e in page_text.lower():
        return True
    return False


def smtp_rcpt_ok(email, sender_address=None, timeout=PROBE_TIMEOUT):
    """SMTP RCPT probe. Returns True only on explicit 250/251.
    Returns False on explicit 5xx. Returns None on indeterminate / timeout
    so the caller can decide."""
    if not email or "@" not in email:
        return False
    domain = email.split("@", 1)[1]
    records = _mx_records(domain)
    if not records:
        return False
    sender = sender_address or PROBE_FROM
    for mx in records[:3]:
        host = str(mx.exchange).rstrip(".")
        try:
            with smtplib.SMTP(host, 25, timeout=timeout) as s:
                s.set_debuglevel(0)
                s.helo("paradisehomedesigninc.com")
                s.mail(sender)
                code, _ = s.rcpt(email)
                if code in (250, 251):
                    return True
                if code in (550, 551, 553):
                    return False
                return None
        except (smtplib.SMTPException, socket.error, OSError) as e:
            log.debug("SMTP probe error on %s: %s", host, e)
            continue
    return None


def verify_email(email, page_text="", scraped_emails=None,
                 sender_address=None, require_on_site=True):
    """Full 3-layer verification.

    Returns (ok: bool, method: str, notes: str). A True result means:
      - format passes
      - MX records resolve
      - email appears in scraped material (skippable via require_on_site=False)
      - SMTP probe is True or indeterminate (only explicit 5xx fails)

    require_on_site: set False when the address came from a CSV without a
    captured page — caller is asserting the source is trusted.
    """
    e = (email or "").strip().lower()
    if e in PLACEHOLDER_EMAILS:
        return False, "", "placeholder"
    if not looks_valid_format(e):
        return False, "", "bad_format"
    if not mx_ok(e):
        return False, "", "no_mx"
    if require_on_site and not found_on_scraped(e, page_text, scraped_emails):
        return False, "", "not_on_site"

    if ENABLE_SMTP_PROBE:
        probe = smtp_rcpt_ok(e, sender_address)
        if probe is False:
            return False, "", "smtp_rejected"
        method = "mx+site+smtp" if probe is True else "mx+site+smtp_indeterminate"
        return True, method, ""
    return True, ("mx+site" if require_on_site else "mx"), ""


def verify(email, sender_address, strict_smtp=False):
    """Legacy MX-only verifier kept for back-compat. New code should call
    verify_email() with page_text/scraped_emails for the full 3-layer pass."""
    if not mx_ok(email):
        return False
    if strict_smtp or ENABLE_SMTP_PROBE:
        probe = smtp_rcpt_ok(email, sender_address)
        if probe is False:
            return False
    return True


def smtp_rcpt_legacy(email, sender_address, timeout=6):
    """Old name kept in case something imports it."""
    return smtp_rcpt_ok(email, sender_address, timeout=timeout)
