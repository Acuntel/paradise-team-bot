# Paradise Team Email Bot

## What this is

Soft-touch reply-bait bot for **Paradise Home Design Inc.** signed by
**The Team** (team@paradisehomedesigninc.com). Single question, no pitch,
no discount, no meeting ask. The reply itself is the lead.

The angle: "What's the one thing about your kitchen you'd change
tomorrow if money weren't a factor?" If they reply, Paradise sends a
free quick sketch.

## Pitch contract

See `MASTER_PROMPT.md`. Body cap 95 words. ASCII hyphens only.

## Sender

- Name: The Team at Paradise Home Design
- Email: team@paradisehomedesigninc.com
- Sign-off: "- The Team / Paradise Home Design Inc / 38 years on Deerpath"

## Commands

```bash
python run.py load data/sample_leads.csv
python run.py verify
python run.py preview 6
python run.py draft 50
```
