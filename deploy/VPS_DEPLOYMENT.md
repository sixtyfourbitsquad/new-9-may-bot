# VPS deployment (webhook-only)

## Overview

The bot runs as an ASGI app (`uvicorn`) exposing:

- `POST {WEBHOOK_PATH}` — Telegram updates (must be HTTPS at the public URL).
- `GET /health` — load balancer / monitoring probe.

Background asyncio workers run **inside the same process**: broadcast consumer, scheduled job poller, retention drainer.

## Clone

```bash
git clone https://github.com/sixtyfourbitsquad/new-9-may-bot.git
cd new-9-may-bot
```

For several bots on one server, see `MULTI_BOT_VPS_SETUP.md`.

## Steps

1. **DNS**: Point `bot.example.com` to your VPS public IPv4/IPv6.

2. **TLS**: Obtain certificates (Let’s Encrypt). Nginx terminates HTTPS and reverse-proxies to uvicorn on `127.0.0.1:8000` (see `deploy/nginx.example.conf`).

3. **PostgreSQL + Redis**: Follow `POSTGRESQL.md` and `REDIS.md`.

4. **Environment**: Copy `.env.example` → `.env` and fill secrets:

   - `BOT_TOKEN` from BotFather.
   - `WEBHOOK_BASE_URL=https://bot.example.com`
   - `WEBHOOK_SECRET` long random string (embedded in webhook URL).
   - `ADMIN_CHAT_ID` — create a private supergroup, add the bot + admins, use `/chatid` helper bot or `getUpdates` once to read the numeric id (negative).
   - `INITIAL_OWNER_ID` — your Telegram user id for automatic owner seeding.

5. **Install Python 3.12+**:

```bash
sudo apt install python3.12-venv python3-pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

6. **Run**:

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

On startup the app calls `setWebhook` so Telegram routes updates to your public URL.

7. **systemd**: See `deploy/telegram-bot.service` — adjust paths and user.

## Multi-instance notes

Telegram allows **one webhook URL per bot token**. Horizontal scaling typically means:

- One webhook-facing instance **or** a load-balanced endpoint sharing the same Redis-backed queues.
- Scale worker throughput via `BROADCAST_CONCURRENCY` and VPS CPU; avoid exceeding Telegram flood limits.

Use nginx `proxy_read_timeout` ≥ 120s for occasional slow Telegram deliveries.

## Graceful restarts

systemd `Restart=always` plus uvicorn main process reload is acceptable; jobs persist in PostgreSQL + Redis queues.
