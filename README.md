# Trieste Mensa Bot

Telegram bot for **Mensa Centrale Trieste** menus. Reads the weekly menu from the [LAMensa WhatsApp channel](https://whatsapp.com/channel/0029Vb5cElw5a23zVecmn70P) via WAHA, parses PDFs and images (OCR), and replies in English.

## Stack

- **aiogram** — Telegram bot (webhook)
- **WAHA** — WhatsApp channel bridge
- **HAProxy** — TLS termination and reverse proxy
- **Docker Compose** — VPS deployment

## Commands

| Command | Description |
|---------|-------------|
| `/today` | Today's menu |
| `/week` | This week's menu |
| `/month`, `/menus` | Full menu from latest channel post |
| `/links` | Recent channel posts / PDF links |
| `/help` | Help text |

## Local development

```bash
uv sync
cp .env.example .env
# Edit .env with TELEGRAM_BOT_TOKEN and other values

# Run WAHA separately (Docker) or point WAHA_URL to an existing instance
uv run python -m src.main
```

## VPS deployment (Ubuntu + Docker)

### 1. Prerequisites

- Docker and Docker Compose
- A domain pointing to your VPS
- TLS certificate at `docker/certs/combined.pem` (full chain + private key)

```bash
mkdir -p docker/certs
cat fullchain.pem privkey.pem > docker/certs/combined.pem
```

### 2. Configure environment

```bash
cp .env.example .env

# Generate WAHA API key and append to .env (required for WhatsApp channel)
grep -q '^WAHA_API_KEY=' .env || echo "WAHA_API_KEY=$(openssl rand -hex 16)" >> .env
```

Set at minimum:

- `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
- `WEBHOOK_HOST` — public URL, e.g. `https://bot.example.com`
- `WAHA_API_KEY` — shared secret for WAHA + bot (generate with `openssl rand -hex 16`)

```bash
# Example .env entries
WAHA_API_KEY=a1b2c3d4e5f6...
WEBHOOK_HOST=https://shole.nikzad.dev:8443
ARDIS_FALLBACK=false
TRANSLATE_URL=
```

If WAHA returns **401 Unauthorized**, the bot cannot read the WhatsApp channel and will
not use ARDiS unless you set `ARDIS_FALLBACK=true`. Fix the key, then restart:

```bash
docker compose up -d --build
docker compose exec bot rm -f /data/cache/*.json
```

### 3. Start WAHA and scan QR

```bash
docker compose up -d waha
```

Open `http://127.0.0.1:3000` on the VPS (SSH tunnel or local browser via tunnel):

```bash
ssh -L 3000:127.0.0.1:3000 user@your-vps
```

Scan the QR code with WhatsApp, then follow the [LAMensa channel](https://whatsapp.com/channel/0029Vb5cElw5a23zVecmn70P).

### 4. Start the full stack

```bash
docker compose up -d --build
```

Verify health:

```bash
curl https://your-domain.example/health
# OK
```

The bot registers its webhook automatically on startup.

### 5. Test

Send `/today` to your bot in Telegram. Replies should arrive in under a second when the menu cache is warm.

## Architecture

```
Telegram → HAProxy (443) → bot:8080/webhook
                              ↓
                         menu cache (volume)
                              ↓
                         WAHA → LAMensa WhatsApp channel
                              ↓ (fallback)
                         ARDiS Trieste PDF
```

Menu data is fetched and translated in a background task every 15 minutes (configurable). Commands read from cache for fast replies.

## Environment variables

See [`.env.example`](.env.example) for all options.

## Notes

- WAHA sessions are stored in the `waha_sessions` Docker volume. Re-scan QR if WhatsApp disconnects.
- OCR quality depends on image quality; PDF posts are preferred when available.
- Rotate your Telegram bot token if it was ever committed to git.
