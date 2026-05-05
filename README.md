# paradise-team-bot

Railway-deployed cron for one of the Paradise Home Design Inc email bots.

## Pipeline (daily, 8am Chicago)

1. `cmd_discover` -- pull fresh leads from the bot's default sources
2. `cmd_verify` -- 3-layer email verification (scraped page text + MX)
3. `cmd_draft N` -- generate N emails into Gmail Drafts via IMAP

Brady's Claude-for-Chrome extension picks up the drafts and clicks send
manually throughout the day. Gmail sees user-driven sends, not API.

## Required env vars (set on Railway)

- `ANTHROPIC_API_KEY`
- `<BOT>_GMAIL_APP_PASSWORD` (e.g. `PARTNERSHIPS_GMAIL_APP_PASSWORD`)
- `DAILY_TARGET` (15 for partnerships, 200 for the homeowner bots)
- `RUN_ENABLED` ('true' to actually run; 'false' parks the cron)

## Local dev

```bash
cd paradise-<bot>-email-bot
python run.py preview 1     # dry, writes HTML to ./previews/
python run.py draft 5       # uploads 5 real drafts to Gmail
```

## Volumes

The SQLite tracker lives on a persistent Railway volume mounted at
`/app/state`. The image ships a seeded DB which is copied into the
volume on first boot, then the volume is the source of truth.
