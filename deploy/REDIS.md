# Redis setup

Redis backs broadcast queues, locks, retention scheduling (ZSET), rate limiting, livestream cooldown cache, and optional FSM keys.

## Docker (recommended for parity)

Use `docker-compose.yml` `redis` service with AOF persistence (`appendonly yes`).

## Native VPS install

```bash
sudo apt install redis-server
sudo sed -i 's/^supervised no/supervised systemd/' /etc/redis/redis.conf
sudo systemctl enable --now redis-server
```

Set `REDIS_URL`, for example:

```
REDIS_URL=redis://127.0.0.1:6379/0
```

### Notes

- Use a password (`redis://:password@host:6379/0`) when exposing beyond localhost.
- For multi-instance webhook receivers, **serialize broadcast consumption** by sharing one Redis queue (BLPOP is safe with multiple consumers; combine with optional distributed locks if needed).
