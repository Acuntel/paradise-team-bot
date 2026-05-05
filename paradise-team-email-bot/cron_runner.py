"""
Daily cron entry point for Railway. Mirrors the Hermann pattern.

Pipeline (idempotent, safe to re-run):
  1. discover -- pull fresh leads via the bot's default sources
  2. verify   -- 3-layer email verification (page text + scraped + MX)
  3. draft N  -- generate N emails, upload to Gmail Drafts via IMAP

Brady's Claude-for-Chrome extension picks up drafts from Gmail and clicks
send manually throughout the day -- so Gmail sees user-driven sends, not
API automation. This is the same pattern as Hermann.

Env vars:
  ANTHROPIC_API_KEY                Required.
  <BOT>_GMAIL_APP_PASSWORD         Required (e.g. PARTNERSHIPS_GMAIL_APP_PASSWORD).
  DAILY_TARGET                     Drafts to attempt this run. Default 15.
  DISCOVER_MAX_NEW                 Cap on new prospects per discover run. Default 100.
  DISCOVER_SOURCES                 Comma-separated source names. Default = bot's own.
  RUN_ENABLED                      'false' = exit immediately. Default 'true'.
  STATE_DIR                        Persistent volume mount. Default = project_dir.
"""
import logging
import os
import shutil
import sys
from pathlib import Path

# Importing run.py wires sys.path (parents[2]/_shared) and builds CFG.
import run  # noqa: F401
from core.runner import cmd_discover, cmd_verify, cmd_draft

CFG = run.CFG

DAILY_TARGET = int(os.getenv("DAILY_TARGET", "15"))
DISCOVER_MAX_NEW = int(os.getenv("DISCOVER_MAX_NEW", "100"))
DISCOVER_SOURCES = os.getenv("DISCOVER_SOURCES", "") or CFG.default_discover_sources
RUN_ENABLED = os.getenv("RUN_ENABLED", "true").lower() not in ("false", "0", "no")

# Persist DB across cron runs via a Railway volume mounted at STATE_DIR.
# The image may ship a seeded DB at <project_dir>/<bot>.db -- copy it into
# STATE_DIR on first run so hand-curated prospects survive the move.
state_dir = Path(os.getenv("STATE_DIR", str(CFG.project_dir)))
state_dir.mkdir(parents=True, exist_ok=True)
seed_db = CFG.project_dir / f"{CFG.bot_name}.db"
live_db = state_dir / f"{CFG.bot_name}.db"
if seed_db.exists() and not live_db.exists() and seed_db.resolve() != live_db.resolve():
    shutil.copy2(seed_db, live_db)
    logging.info("seeded %s from image bundle", live_db)
CFG.db_path = live_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    if not RUN_ENABLED:
        log.info("RUN_ENABLED=false -- exiting without running %s", CFG.bot_name)
        return
    log.info("=== %s daily cron ===", CFG.bot_name)
    log.info("model=%s  daily_target=%d  discover_max=%d  sources=%s",
             CFG.model, DAILY_TARGET, DISCOVER_MAX_NEW, DISCOVER_SOURCES)
    log.info("db: %s", CFG.db_path)

    log.info("step 1: discover")
    try:
        cmd_discover(CFG, sources=DISCOVER_SOURCES, max_new=DISCOVER_MAX_NEW)
    except Exception as e:
        log.warning("discover failed (non-fatal): %s", e)

    log.info("step 2: verify")
    try:
        cmd_verify(CFG)
    except Exception as e:
        log.warning("verify failed (non-fatal): %s", e)

    log.info("step 3: draft up to %d", DAILY_TARGET)
    try:
        cmd_draft(CFG, DAILY_TARGET)
    except Exception as e:
        log.error("draft step failed: %s", e)
        sys.exit(1)
    log.info("=== done ===")


if __name__ == "__main__":
    main()
