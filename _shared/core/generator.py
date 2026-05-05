"""
Per-prospect Claude generator. Uses prompt caching on the master prompt
so bulk runs stay cheap. Enforces post-filters: dash rule, banned-word
rule, word-count rule, subject sanity. One retry on rejection.
"""
import json
import logging
import random
import re

from anthropic import Anthropic

log = logging.getLogger(__name__)

BANNED_WORDS = [
    "elevate", "transform", "reach out", "touch base", "circle back",
    "leverage", "synergy", "journey", "unlock", "empower",
    "state-of-the-art", "cutting-edge", "reimagine", "world-class",
    "premier", "tailored", "bespoke", "luxurious", "exquisite",
    "dream home", "crafted", "passion", "excited", "delighted",
    "thrilled", "i hope this email finds you", "amazing", "incredible",
    "rest assured", "cherished",
]
DASH_RE = re.compile(r"[—–]")
BANNED_SUBJECT_WORDS = ["discount"]
WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)


def _violations(parsed, max_words):
    issues = []
    body = parsed.get("body_text", "") or ""
    subject = parsed.get("subject", "") or ""

    if DASH_RE.search(body) or DASH_RE.search(subject):
        issues.append("contains em or en dash")
    if WEEKDAY_RE.search(body) or WEEKDAY_RE.search(subject):
        issues.append("names a specific weekday (close must be 'what's your availability')")
    low = body.lower()
    for w in BANNED_WORDS:
        if w in low:
            issues.append(f"banned word: {w}")
    wc = len(re.findall(r"\w+", body))
    if wc > max_words:
        issues.append(f"word count {wc} > {max_words}")
    if any(w in subject.lower() for w in BANNED_SUBJECT_WORDS):
        issues.append("subject has banned word")
    if any(len(w) > 3 and w.isupper() for w in subject.split()):
        issues.append("subject has ALL-CAPS word")
    if len(subject) > 60:
        issues.append(f"subject too long ({len(subject)})")
    return issues


def generate(api_key, model, master_prompt, payload, max_words):
    client = Anthropic(api_key=api_key)

    def _call(extra=""):
        msg = "Inputs:\n```json\n" + json.dumps(payload, indent=2) + "\n```"
        if extra:
            msg += "\n\nAdditional instruction: " + extra
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=[{
                "type": "text",
                "text": master_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": msg}],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)

    try:
        parsed = _call()
    except Exception as e:
        log.warning("first call failed: %s", e)
        return None

    issues = _violations(parsed, max_words)
    if not issues:
        return _finalize(parsed)

    hint = "Rewrite to fix: " + "; ".join(issues) + ". Be shorter, no dashes."
    try:
        parsed = _call(hint)
    except Exception as e:
        log.warning("retry failed: %s", e)
        return None
    if _violations(parsed, max_words):
        log.info("rejected after 2 tries: %s", _violations(parsed, max_words))
        return None
    return _finalize(parsed)


def _finalize(parsed):
    body = parsed["body_text"]
    return {
        "subject": parsed["subject"].strip(),
        "body_text": body,
        "hero_tag": parsed.get("hero_tag", ""),
        "gallery_tags": parsed.get("gallery_tags", []) or [],
        "word_count": len(re.findall(r"\w+", body)),
    }
