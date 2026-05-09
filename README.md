# Telegram community bot (webhook)

Production-oriented Telegram bot: PostgreSQL, Redis, FastAPI webhook, admin panel, broadcasts, scheduling, retention.

## Quick links

- **Clone:** `git clone https://github.com/sixtyfourbitsquad/new-9-may-bot.git`
- **Single VPS:** see [`deploy/VPS_DEPLOYMENT.md`](deploy/VPS_DEPLOYMENT.md)
- **Multiple bots on one VPS:** see [`MULTI_BOT_VPS_SETUP.md`](MULTI_BOT_VPS_SETUP.md)
- **Private server runbook (passwords, per-machine):** copy [`AYAN_SERVER.md`](AYAN_SERVER.md) locally — it is **gitignored** and will not be pushed.

## Setup

1. Copy `.env.example` → `.env` and fill values (never commit `.env`).
2. Apply `database/schema.sql` to PostgreSQL.
3. Run: `uvicorn main:app --host 0.0.0.0 --port 8000` (behind HTTPS reverse proxy in production).

Repository: [sixtyfourbitsquad/new-9-may-bot](https://github.com/sixtyfourbitsquad/new-9-may-bot)
