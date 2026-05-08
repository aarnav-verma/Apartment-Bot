# SF Sublet Finder

Watches Craigslist for new SF sublet and room listings that fit your criteria, builds a ready-to-send reply, and pings you through Telegram. The default setup costs $0 because it uses Telegram, GitHub Actions, and a free neighborhood whitelist for commute filtering.

## What is implemented

- Craigslist RSS polling for SF and East Bay sublets/rooms every 5 minutes through GitHub Actions
- Full listing fetch and parsing for price, neighborhood, address, lat/lng, post body, and post time
- Filters for max rent, approximate date compatibility, and either Google Maps commute time or a free neighborhood whitelist
- Telegram notifications with two messages: listing summary, then full reply draft
- Optional Anthropic personalization for the first sentence of the reply
- Persistent `data/seen.json` state, committed back to the repo by the workflow so duplicate alerts do not fire
- Seed mode to mark existing listings as seen before the first real alert run
- Local fallback runner for when Craigslist blocks GitHub Actions IPs
- Dry-run smoke test that does not call external APIs
- Experimental Facebook Marketplace scraper kept manual-only because Facebook blocks bots aggressively

## Required setup

### 1. Add GitHub Actions secrets

In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Required | Value |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token from BotFather |
| `TELEGRAM_CHAT_ID` | Yes | Your Telegram chat id from `getUpdates` |
| `GOOGLE_MAPS_API_KEY` | No | Enables exact commute scoring |
| `ANTHROPIC_API_KEY` | No | Enables personalized opener |
| `ANTHROPIC_MODEL` | No | Override model, defaults to `claude-haiku-4-5-20251001` |
| `FB_COOKIES_JSON` | No | Only for experimental Facebook workflow |

Telegram must be started manually once: open your bot in Telegram and tap **Start**. A bot cannot message you until you do this.

### 2. Test Telegram locally

```bash
cp .env.example .env
# edit .env with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
python -m src.notify --env .env --message "Apartment Bot test message"
```

Or using the wrapper:

```bash
python scripts/test_telegram.py --message "Apartment Bot test message"
```

### 3. Run the dry-run smoke test

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m tests.test_dry_run
```

### 4. Do a dry Craigslist run

This prints alerts instead of sending them and does not mutate `data/seen.json`:

```bash
python -m src.poll --source craigslist --dry-run --verbose
```

### 5. Seed existing listings

Before enabling real alerts, run seed mode once so the bot does not alert on every already-live listing.

In GitHub: **Actions → Poll Craigslist → Run workflow → seed = true**

Or locally:

```bash
python -m src.poll --source craigslist --seed --verbose
```

### 6. Let the cron run

The `Poll Craigslist` workflow runs every 5 minutes. New matching listings will send Telegram alerts.

## Local fallback if Craigslist blocks GitHub Actions

Craigslist sometimes blocks GitHub/Azure datacenter IPs. If Actions logs show 403s from Craigslist, run locally from your Mac:

```bash
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
scripts/run_local.sh --dry-run --verbose
```

For scheduled local runs, edit `scripts/com.aarnav.subletfinder.plist` and replace `/ABSOLUTE_PATH/sf-sublet-finder` with the actual project path. Then install it:

```bash
cp scripts/com.aarnav.subletfinder.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aarnav.subletfinder.plist
tail -f /tmp/subletfinder.log
```

Keep your laptop awake while searching:

```bash
caffeinate -d -i &
```

## Customization

Edit `config.yaml` for:

- Rent cap
- Move-in and move-out windows
- Commute limit
- Craigslist areas/categories/search terms
- Reply template and fallback opener
- Notification channel

The reply template uses `[HOOK]` as the placeholder for either the Anthropic-generated opener or the fallback opener.

## File map

```text
sf-sublet-finder/
├── README.md
├── config.yaml
├── requirements.txt
├── .env.example
├── data/seen.json
├── src/
│   ├── env.py
│   ├── poll.py
│   ├── filters.py
│   ├── reply.py
│   ├── notify.py
│   ├── state.py
│   └── sources/
│       ├── craigslist.py
│       └── facebook.py
├── .github/workflows/
│   ├── poll-craigslist.yml
│   └── poll-facebook.yml
├── scripts/
│   ├── run_local.sh
│   ├── test_telegram.py
│   └── com.aarnav.subletfinder.plist
├── docs/
│   ├── ALERTS.md
│   └── FACEBOOK.md
└── tests/
    └── test_dry_run.py
```
