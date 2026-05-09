# Multi-Bot VPS Setup Guide

This guide explains how to deploy **multiple instances** of the same Telegram bot on one VPS, with each bot isolated by folder, database, service, and webhook URL.

---

## Architecture Overview

Use one VPS with shared infrastructure:

- **Nginx** (reverse proxy + TLS)
- **PostgreSQL** (one database per bot)
- **Redis** (shared instance, unique prefixes per bot)
- **systemd** (one service per bot)

Suggested layout:

```bash
/opt/tg-bots/
  bot-alpha/
  bot-beta/
  bot-gamma/
```

Each folder is the same codebase, with a different `.env`.

---

## Source Repository

Official project URL (clone this on your VPS):

```text
https://github.com/sixtyfourbitsquad/new-9-may-bot.git
```

Clone once per bot instance (or clone once and copy the folder — both work):

```bash
git clone https://github.com/sixtyfourbitsquad/new-9-may-bot.git bot-alpha
```

Do **not** commit secrets. Copy `.env.example` → `.env` on the server and edit locally (`.env` is gitignored).

---

## 1) Install Base Dependencies (once)

```bash
sudo apt update
sudo apt install -y python3.12-venv python3-pip nginx postgresql redis-server certbot python3-certbot-nginx
sudo systemctl enable --now postgresql redis-server nginx
```

---

## 2) Prepare First Bot Folder

```bash
sudo mkdir -p /opt/tg-bots
sudo chown -R $USER:$USER /opt/tg-bots
cd /opt/tg-bots

git clone https://github.com/sixtyfourbitsquad/new-9-may-bot.git bot-alpha
cd /opt/tg-bots/bot-alpha

cp .env.example .env
# edit .env with token, webhook URL, DB, Redis prefixes, PORT, etc.

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3) PostgreSQL Per-Bot Isolation

Create one DB user + DB per bot.

Example for **bot-alpha** with password `1`:

```bash
sudo -u postgres psql -c "CREATE USER tg_alpha WITH PASSWORD '1';" || true
sudo -u postgres psql -c "CREATE DATABASE tg_alpha OWNER tg_alpha;" || true
psql "postgresql://tg_alpha:1@127.0.0.1:5432/tg_alpha" -f database/schema.sql
```

Repeat similarly for bot-beta (`tg_beta`, password `2`), bot-gamma (`tg_gamma`, password `3`), etc.

> Note: `1/2/3` works for quick setup. For production security, use strong passwords.

---

## 4) Per-Bot `.env` (Critical)

Create `.env` in each bot folder with bot-specific values.

**Telegram user ids (admins / owner)** — these are **your real numeric ids**, not placeholders:

- **`ADMIN_USER_IDS`** — **Required.** Comma-separated list of Telegram **user** ids (digits only) who receive forwarded user messages in private chat with the bot. Each listed user must **`/start`** the bot once. Get ids from a bot such as `@userinfobot` / `@RawDataBot` or Telegram “copy id” flows — paste **your** numbers here, not examples from docs.
- **`INITIAL_OWNER_ID`** — **Optional.** If set to **your** numeric user id, the app can seed the **owner** role in the database when there are no owners yet. Often the same number as you put in `ADMIN_USER_IDS` (your own id). You may omit this field if you do not need automatic owner bootstrap.

Example (`/opt/tg-bots/bot-alpha/.env`) — replace secrets and ids with yours:

```env
BOT_TOKEN=
WEBHOOK_BASE_URL=https://alpha.yourdomain.com
WEBHOOK_PATH=/tg/webhook/{secret}
WEBHOOK_SECRET=

ADMIN_USER_IDS=
INITIAL_OWNER_ID=

POSTGRES_DSN=postgresql://tg_alpha:1@127.0.0.1:5432/tg_alpha
POSTGRES_POOL_MIN=2
POSTGRES_POOL_MAX=20

REDIS_URL=redis://127.0.0.1:6379/0
REDIS_BROADCAST_QUEUE=b1:broadcast:jobs
REDIS_SCHEDULER_QUEUE=b1:scheduler:jobs
REDIS_FSM_PREFIX=b1:fsm:
REDIS_RATE_PREFIX=b1:rate:
REDIS_LIVESTREAM_PREFIX=b1:livestream:

HOST=0.0.0.0
PORT=8101
LOG_LEVEL=INFO
STORAGE_DIR=./storage

BROADCAST_CONCURRENCY=25
BROADCAST_CHUNK_SIZE=500
USER_MESSAGE_RATE_PER_MINUTE=30
ADMIN_REPLY_RATE_PER_MINUTE=120
LIVESTREAM_COOLDOWN_SECONDS=300
```

Before `systemctl enable/start`, fill **`BOT_TOKEN`**, **`WEBHOOK_SECRET`**, and **`ADMIN_USER_IDS`** (at least one real user id). Leaving `ADMIN_USER_IDS` empty will fail app startup.

For second bot, use:

- different `BOT_TOKEN`
- different domain (e.g. `beta.yourdomain.com`)
- different DB DSN
- different local `PORT` (e.g. `8102`)
- different Redis prefixes (`b2:...`)

---

## 5) systemd Service Per Bot

Create one service file per bot.

Example: `/etc/systemd/system/tg-bot-alpha.service`

```ini
[Unit]
Description=Telegram Bot Alpha
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/tg-bots/bot-alpha
EnvironmentFile=/opt/tg-bots/bot-alpha/.env
ExecStart=/opt/tg-bots/bot-alpha/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8101
Restart=always
RestartSec=3
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-bot-alpha
sudo systemctl status tg-bot-alpha --no-pager
```

Repeat for each bot (change folder, service name, and port).

---

## 6) Nginx Reverse Proxy (One Domain per Bot)

Example file: `/etc/nginx/sites-available/alpha.yourdomain.com`

```nginx
server {
    listen 80;
    server_name alpha.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8101;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and reload:

```bash
sudo ln -sf /etc/nginx/sites-available/alpha.yourdomain.com /etc/nginx/sites-enabled/alpha.yourdomain.com
sudo nginx -t
sudo systemctl reload nginx
```

Issue certificate:

```bash
sudo certbot --nginx -d alpha.yourdomain.com
```

Repeat for each domain/subdomain.

---

## 7) Firewall / Cloud Security Rules

Allow required ports:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

If using cloud providers (AWS/GCP/Azure), also open inbound TCP 80/443 in security groups/firewall rules.

---

## 8) DNS Requirements

For each bot domain/subdomain:

- Create `A` record -> VPS public IPv4
- (Optional) create `AAAA` record -> VPS IPv6

Verify:

```bash
getent ahosts alpha.yourdomain.com
```

---

## 9) Webhook Behavior in This Project

The app is webhook-only and calls `setWebhook` on startup automatically.

So whenever you:

- change `WEBHOOK_BASE_URL` / `WEBHOOK_PATH` / `WEBHOOK_SECRET`
- restart the service

the webhook is updated.

Check webhook:

```bash
source /opt/tg-bots/bot-alpha/.venv/bin/activate
set -a && source /opt/tg-bots/bot-alpha/.env && set +a
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

---

## 10) Add a New Bot Quickly

1. Clone again (recommended — clean history) or copy folder:
   ```bash
   cd /opt/tg-bots
   git clone https://github.com/sixtyfourbitsquad/new-9-may-bot.git bot-new
   # or: cp -r /opt/tg-bots/bot-alpha /opt/tg-bots/bot-new && rm -rf bot-new/.git
   ```
2. Create new Postgres user/database and run schema.
3. Edit `/opt/tg-bots/bot-new/.env`:
   - token, admin id, owner id
   - domain
   - database DSN
   - unique local port
   - unique Redis prefixes
4. Add systemd service for new bot.
5. Add nginx vhost for new domain.
6. Run certbot for new domain.
7. Start service and verify logs.

---

## 11) Operations / Monitoring Commands

```bash
# service logs
journalctl -u tg-bot-alpha -f

# restart
sudo systemctl restart tg-bot-alpha

# nginx
sudo nginx -t && sudo systemctl reload nginx

# webhook check
curl -s "https://api.telegram.org/bot<token>/getWebhookInfo"
```

---

## 12) Security Recommendations

- Rotate exposed bot tokens immediately.
- Keep `.env` permissions strict:
  ```bash
  chmod 600 /opt/tg-bots/*/.env
  ```
- Use strong DB passwords in production.
- Use separate Telegram admin groups per bot.
- Backup each bot database independently.

---

## 13) Common Pitfalls

- Certbot timeout -> port 80 blocked or DNS not pointing correctly.
- Webhook set but no updates -> HTTPS endpoint not reachable publicly.
- Multiple bots mixing data -> Redis prefixes not unique.
- Wrong bot answering -> token/service/env mismatch.

