# Telegram community bot (webhook)

Production-oriented Telegram bot stack: **PostgreSQL**, **Redis**, **FastAPI** (`uvicorn`), **python-telegram-bot** webhooks, admin panel, broadcasts, scheduling, retention, onboarding drip.

---

## Requirements

- **Python 3.12+**
- **PostgreSQL** (schema in `database/schema.sql`)
- **Redis**
- **Public HTTPS URL** for Telegram (`WEBHOOK_BASE_URL`); Telegram calls `setWebhook` toward that host.

---

## Deploy on Ubuntu VPS (one bot)

Use one directory per deployment (example: `/opt/tg-bots/mybot`). Adjust names and ports to taste.

### 1. Install packages

```bash
sudo apt update
sudo apt install -y python3.12-venv python3-pip nginx postgresql redis-server certbot python3-certbot-nginx
sudo systemctl enable --now postgresql redis-server nginx
```

### 2. Clone and Python env

```bash
sudo mkdir -p /opt/tg-bots
sudo chown -R "$USER:$USER" /opt/tg-bots
cd /opt/tg-bots
git clone https://github.com/sixtyfourbitsquad/new-9-may-bot.git mybot
cd mybot

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Database

Create a database user and database, then apply the schema (example user/db `tg_bot`; use a strong password in production):

```bash
sudo -u postgres psql -c "CREATE USER tg_bot WITH PASSWORD 'YOUR_DB_PASSWORD';" || true
sudo -u postgres psql -c "CREATE DATABASE tg_bot OWNER tg_bot;" || true
psql "postgresql://tg_bot:YOUR_DB_PASSWORD@127.0.0.1:5432/tg_bot" -f database/schema.sql
```

Details: [`deploy/POSTGRESQL.md`](deploy/POSTGRESQL.md).

### 4. Configuration

```bash
cp .env.example .env
chmod 600 .env
nano .env   # or your editor
```

Set at minimum:

| Variable | Notes |
|----------|--------|
| `BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `WEBHOOK_BASE_URL` | Public origin only, no trailing slash, e.g. `https://bot.example.com` |
| `WEBHOOK_PATH` | Default `/tg/webhook/{secret}` — `{secret}` is replaced by `WEBHOOK_SECRET` |
| `WEBHOOK_SECRET` | Long random string (appears in the webhook URL Telegram calls) |
| `ADMIN_USER_IDS` | Comma-separated numeric Telegram **user** ids (admins must `/start` the bot) |
| `POSTGRES_DSN` | On VPS typically `postgresql://tg_bot:...@127.0.0.1:5432/tg_bot` |
| `REDIS_URL` | Usually `redis://127.0.0.1:6379/0` |
| `PORT` | Local port for uvicorn (must match nginx `proxy_pass`; e.g. `8000`) |

Optional: `INITIAL_OWNER_ID` — seeds owner role when the admins table is empty.

Redis queue/key prefixes must be **unique per bot** if several bots share one Redis (see multi-bot guide below).

### 5. systemd

Copy and edit the sample unit (paths, user, port):

```bash
sudo cp deploy/telegram-bot.service /etc/systemd/system/mybot.service
sudo nano /etc/systemd/system/mybot.service
```

Point `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` (`uvicorn` `--port`) at your install. Example `ExecStart`:

```text
ExecStart=/opt/tg-bots/mybot/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mybot.service
sudo systemctl status mybot.service --no-pager
```

### 6. Nginx + TLS

- Put `server_name` to the same host as `WEBHOOK_BASE_URL`.
- Proxy `/tg/webhook/` (and usually `/health`) to `http://127.0.0.1:<PORT>`.

Example fragments: [`deploy/nginx.example.conf`](deploy/nginx.example.conf).

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d bot.example.com
```

Allow **80** and **443** on the firewall (`sudo ufw allow 80,443/tcp`).

### 7. DNS

Create an **A** record for your bot hostname pointing at the VPS public IPv4. Telegram’s servers must resolve this name when registering the webhook.

Verify:

```bash
dig +short bot.example.com A
curl -4 -sS ifconfig.me   # should match if single public IP
```

### 8. Verify

After the service is running:

```bash
curl -sS http://127.0.0.1:8000/health
```

You should see JSON with `"status":"ok"`. While `setWebhook` runs in the background, `"webhook"` may be `registering`, then `registered` or `not_registered` if Telegram rejects the URL (often DNS propagation or hostname resolution from Telegram’s side).

Check Telegram’s view (loads `.env`; **do not log this output publicly** — it contains the token):

```bash
cd /opt/tg-bots/mybot && set -a && source .env && set +a
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

Logs:

```bash
journalctl -u mybot.service -n 80 --no-pager
```

In Telegram, open the bot and send **`/admin`** (must be an admin user id from `ADMIN_USER_IDS`).

---

## Multiple bots on one VPS

Use **separate** folders, databases, local ports, Redis prefixes, systemd units, and nginx `server_name` entries. Step-by-step: [`MULTI_BOT_VPS_SETUP.md`](MULTI_BOT_VPS_SETUP.md).

---

## Extra docs

| Doc | Purpose |
|-----|---------|
| [`deploy/VPS_DEPLOYMENT.md`](deploy/VPS_DEPLOYMENT.md) | Webhook overview and scaling notes |
| [`deploy/POSTGRESQL.md`](deploy/POSTGRESQL.md) | PostgreSQL setup |
| [`deploy/REDIS.md`](deploy/REDIS.md) | Redis setup |
| [`deploy/telegram-bot.service`](deploy/telegram-bot.service) | systemd template |
| [`deploy/nginx.example.conf`](deploy/nginx.example.conf) | nginx + TLS example |

---

## Local development (optional)

Without a public HTTPS URL, set `WEBHOOK_REGISTER_ON_STARTUP=false` in `.env` so startup does not call Telegram `setWebhook`. For full Telegram delivery you still need a reachable HTTPS webhook in production.

---

## Repository

[sixtyfourbitsquad/new-9-may-bot](https://github.com/sixtyfourbitsquad/new-9-may-bot)
