"""
Per-bot SQLite tracker AND shared cross-bot dedupe.

Each bot has its own DB for prospects and drafts. A second connection
hits the SHARED dedupe.db so a homeowner emailed by Brady's bot is
never also emailed by Sales / Team / Partnerships, and vice versa.

The shared dedupe.db also holds an `enrich_miss` table so the
finder/stream_enrich pipeline can skip addresses we tried recently and
came up empty (30-day TTL).
"""
import sqlite3
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

SHARED_DEDUPE_PATH = (
    Path(__file__).resolve().parents[2] / "_shared" / "data" / "dedupe.db"
)

ENRICH_MISS_TTL_SECONDS = 30 * 24 * 3600

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name TEXT,
  last_name TEXT,
  email TEXT UNIQUE NOT NULL,
  town TEXT,
  home_value_tier TEXT,
  year_built INTEGER,
  recently_sold INTEGER DEFAULT 0,
  company_name TEXT,
  company_website TEXT,
  pipeline_note TEXT,
  personal_hook TEXT,
  source_type TEXT,
  source_url TEXT,
  scraped_page_text TEXT,
  scraped_emails TEXT,
  email_verified INTEGER DEFAULT 0,
  do_not_contact INTEGER DEFAULT 0,
  bounced INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prospect_id INTEGER NOT NULL,
  subject TEXT,
  hero_tag TEXT,
  gallery_tags TEXT,
  word_count INTEGER,
  drafted_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(prospect_id) REFERENCES prospects(id)
);

CREATE TABLE IF NOT EXISTS daily_stats (
  day TEXT PRIMARY KEY,
  drafts_created INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_prospects_email ON prospects(email);
"""

DEDUPE_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacted (
  email TEXT PRIMARY KEY,
  bot   TEXT NOT NULL,
  drafted_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dnc (
  email TEXT PRIMARY KEY,
  reason TEXT,
  added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS enrich_miss (
  key TEXT PRIMARY KEY,
  reason TEXT,
  recorded_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS addresses_completed (
  key TEXT PRIMARY KEY,
  address TEXT,
  town TEXT,
  email TEXT,
  market_value INTEGER,
  tier TEXT,
  completed_at INTEGER NOT NULL
);
"""


@contextmanager
def _conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init(local_db_path):
    SHARED_DEDUPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn(local_db_path) as c:
        c.executescript(LOCAL_SCHEMA)
        # Forward-compatible column adds for older DBs created before the
        # scraped_page_text/scraped_emails fields existed.
        existing = {row[1] for row in c.execute("PRAGMA table_info(prospects)")}
        for col, ctype in (
            ("scraped_page_text", "TEXT"),
            ("scraped_emails", "TEXT"),
        ):
            if col not in existing:
                c.execute(f"ALTER TABLE prospects ADD COLUMN {col} {ctype}")
    with _conn(SHARED_DEDUPE_PATH) as c:
        c.executescript(DEDUPE_SCHEMA)


def already_contacted_by_any_bot(email):
    with _conn(SHARED_DEDUPE_PATH) as c:
        r = c.execute(
            "SELECT bot FROM contacted WHERE email=?", (email.lower(),)
        ).fetchone()
        return r["bot"] if r else None


def is_dnc(email):
    with _conn(SHARED_DEDUPE_PATH) as c:
        r = c.execute(
            "SELECT 1 FROM dnc WHERE email=?", (email.lower(),)
        ).fetchone()
        return r is not None


def mark_globally_contacted(email, bot_name):
    with _conn(SHARED_DEDUPE_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO contacted (email, bot) VALUES (?, ?)",
            (email.lower(), bot_name),
        )


def add_dnc(email, reason="manual"):
    with _conn(SHARED_DEDUPE_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO dnc (email, reason) VALUES (?, ?)",
            (email.lower(), reason),
        )


def upsert_prospect(local_db_path, row):
    with _conn(local_db_path) as c:
        existing = c.execute(
            "SELECT id FROM prospects WHERE email=?", (row["email"].lower(),)
        ).fetchone()
        if existing:
            # Backfill enrichment fields if a later run learned them.
            updates = []
            params = []
            for k in ("pipeline_note", "personal_hook",
                      "scraped_page_text", "scraped_emails"):
                v = row.get(k)
                if v:
                    updates.append(f"{k}=COALESCE(NULLIF({k}, ''), ?)")
                    params.append(v)
            if updates:
                params.append(existing["id"])
                c.execute(
                    f"UPDATE prospects SET {', '.join(updates)} WHERE id=?",
                    params,
                )
            return existing["id"]
        cur = c.execute(
            """INSERT INTO prospects
              (first_name, last_name, email, town, home_value_tier, year_built,
               recently_sold, company_name, company_website, pipeline_note,
               personal_hook, source_type, source_url,
               scraped_page_text, scraped_emails)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row.get("first_name"),
                row.get("last_name"),
                row["email"].lower(),
                row.get("town"),
                row.get("home_value_tier"),
                row.get("year_built"),
                int(row.get("recently_sold") or 0),
                row.get("company_name"),
                row.get("company_website"),
                row.get("pipeline_note"),
                row.get("personal_hook"),
                row.get("source_type"),
                row.get("source_url"),
                row.get("scraped_page_text"),
                row.get("scraped_emails"),
            ),
        )
        return cur.lastrowid


def mark_verified(local_db_path, email):
    with _conn(local_db_path) as c:
        c.execute(
            "UPDATE prospects SET email_verified=1 WHERE email=?",
            (email.lower(),),
        )


def mark_bounced(local_db_path, email):
    with _conn(local_db_path) as c:
        c.execute(
            "UPDATE prospects SET bounced=1, do_not_contact=1 WHERE email=?",
            (email.lower(),),
        )
    add_dnc(email, "bounce")


def pending_prospects(local_db_path, limit=None):
    q = """SELECT * FROM prospects
           WHERE email_verified=1 AND do_not_contact=0 AND bounced=0
             AND id NOT IN (SELECT prospect_id FROM drafts)
           ORDER BY created_at ASC"""
    if limit:
        q += f" LIMIT {int(limit)}"
    with _conn(local_db_path) as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def all_prospects(local_db_path):
    with _conn(local_db_path) as c:
        return [
            dict(r) for r in
            c.execute("SELECT * FROM prospects ORDER BY created_at DESC").fetchall()
        ]


def log_draft(local_db_path, prospect_id, subject, hero_tag, gallery_tags,
              word_count, bot_name, email):
    today = date.today().isoformat()
    with _conn(local_db_path) as c:
        c.execute(
            """INSERT INTO drafts
              (prospect_id, subject, hero_tag, gallery_tags, word_count)
              VALUES (?,?,?,?,?)""",
            (prospect_id, subject, hero_tag,
             ",".join(gallery_tags or []), word_count),
        )
        c.execute(
            """INSERT INTO daily_stats (day, drafts_created) VALUES (?, 1)
               ON CONFLICT(day) DO UPDATE SET
                 drafts_created = drafts_created + 1""",
            (today,),
        )
    mark_globally_contacted(email, bot_name)


def drafts_today(local_db_path):
    today = date.today().isoformat()
    with _conn(local_db_path) as c:
        r = c.execute(
            "SELECT drafts_created FROM daily_stats WHERE day=?", (today,)
        ).fetchone()
        return r["drafts_created"] if r else 0


# ---------------------------------------------------------------------------
# Cross-bot helpers used by the finder pipeline
# ---------------------------------------------------------------------------

def already_emailed_set():
    """All emails that any Paradise bot has ever drafted to. The finder
    uses this so stream_enrich can skip prospects who'd be deduped anyway."""
    with _conn(SHARED_DEDUPE_PATH) as c:
        rows = c.execute("SELECT email FROM contacted").fetchall()
        emails = {r["email"].lower() for r in rows}
        rows = c.execute("SELECT email FROM dnc").fetchall()
        emails.update(r["email"].lower() for r in rows)
        return emails


def is_enrich_miss_fresh(address, town):
    """True if we logged an enrich miss for this address less than the TTL ago.
    Saves the cron from re-trying the same dead-end address every run."""
    key = f"{(address or '').lower().strip()}|{(town or '').lower().strip()}"
    if not key.strip("|"):
        return False
    cutoff = int(time.time()) - ENRICH_MISS_TTL_SECONDS
    with _conn(SHARED_DEDUPE_PATH) as c:
        r = c.execute(
            "SELECT 1 FROM enrich_miss WHERE key=? AND recorded_at >= ?",
            (key, cutoff),
        ).fetchone()
        return r is not None


def record_enrich_miss(address, town, reason="no_email"):
    key = f"{(address or '').lower().strip()}|{(town or '').lower().strip()}"
    if not key.strip("|"):
        return
    with _conn(SHARED_DEDUPE_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO enrich_miss (key, reason, recorded_at) "
            "VALUES (?,?,?)",
            (key, reason, int(time.time())),
        )


def purge_stale_enrich_misses():
    cutoff = int(time.time()) - ENRICH_MISS_TTL_SECONDS
    with _conn(SHARED_DEDUPE_PATH) as c:
        c.execute("DELETE FROM enrich_miss WHERE recorded_at < ?", (cutoff,))


def _addr_key(address, town):
    return f"{(address or '').lower().strip()}|{(town or '').lower().strip()}"


def is_address_completed(address, town):
    """True if an earlier discover run already turned this address into
    a verified prospect. Lets address-yielding sources skip duplicate
    work without re-scraping owner/profile/email."""
    key = _addr_key(address, town)
    if not key.strip("|"):
        return False
    with _conn(SHARED_DEDUPE_PATH) as c:
        r = c.execute(
            "SELECT 1 FROM addresses_completed WHERE key=?", (key,),
        ).fetchone()
        return r is not None


def mark_address_completed(address, town, email, market_value=None,
                            tier=None):
    """Called the moment a verified prospect is upserted. Idempotent."""
    key = _addr_key(address, town)
    if not key.strip("|"):
        return
    with _conn(SHARED_DEDUPE_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO addresses_completed "
            "(key, address, town, email, market_value, tier, completed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (key, address, town, email.lower() if email else None,
             market_value, tier, int(time.time())),
        )


def get_completed_address(address, town):
    """Return the saved {email, market_value, tier} for a completed
    address, or None."""
    key = _addr_key(address, town)
    with _conn(SHARED_DEDUPE_PATH) as c:
        r = c.execute(
            "SELECT email, market_value, tier FROM addresses_completed "
            "WHERE key=?", (key,),
        ).fetchone()
        if not r:
            return None
        return {"email": r["email"], "market_value": r["market_value"],
                 "tier": r["tier"]}
