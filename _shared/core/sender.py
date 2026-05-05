"""
Gmail IMAP draft creator. Each bot's config provides its own GMAIL_ADDRESS
+ GMAIL_APP_PASSWORD. Draft-only; never auto-sends.
"""
import email.utils
import imaplib
import logging
import mimetypes
import os
import time
from email.message import EmailMessage

log = logging.getLogger(__name__)


def _build_message(*, sender_name, gmail_address, to_email, subject,
                   html_body, text_body, images, message_id_domain,
                   extra_headers=None):
    """images: list of (cid, abs_path) tuples.
    extra_headers: optional dict of additional headers to set
    (List-Unsubscribe + List-Unsubscribe-Post when wired to dashboard)."""
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((sender_name, gmail_address))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=message_id_domain)
    if extra_headers:
        for k, v in extra_headers.items():
            if v:
                msg[k] = v
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    html_part = msg.get_payload()[1]
    for cid, path in images:
        if not os.path.exists(path):
            log.warning("missing image, skipping: %s", path)
            continue
        mime = mimetypes.guess_type(path)[0] or "image/png"
        maintype, subtype = mime.split("/", 1)
        with open(path, "rb") as f:
            data = f.read()
        html_part.add_related(
            data, maintype=maintype, subtype=subtype, cid=f"<{cid}>"
        )
    return msg


def _html_to_text(html):
    import re
    t = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", t).strip()


def create_drafts_batch(items, *, sender_name, gmail_address,
                        gmail_app_password, message_id_domain):
    """items: list of dicts with to_email, subject, html_body, images,
    text_body? Returns count of successful uploads."""
    if not gmail_app_password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD not set in .env. Generate one at "
            "myaccount.google.com/apppasswords."
        )
    if not items:
        return 0

    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    created = 0
    try:
        imap.login(gmail_address, gmail_app_password)
        for i, it in enumerate(items, start=1):
            text_body = it.get("text_body") or _html_to_text(it["html_body"])
            msg = _build_message(
                sender_name=sender_name,
                gmail_address=gmail_address,
                to_email=it["to_email"],
                subject=it["subject"],
                html_body=it["html_body"],
                text_body=text_body,
                images=it.get("images") or [],
                message_id_domain=message_id_domain,
                extra_headers=it.get("extra_headers"),
            )
            try:
                res = imap.append(
                    "[Gmail]/Drafts", "\\Draft",
                    imaplib.Time2Internaldate(time.time()),
                    msg.as_bytes(),
                )
                if res[0] == "OK":
                    created += 1
                    log.info("draft [%d/%d] %s", i, len(items), it["to_email"])
                else:
                    log.warning("draft FAIL %s: %s", it["to_email"], res)
            except Exception as e:
                log.warning("draft ERROR %s: %s", it["to_email"], e)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return created
