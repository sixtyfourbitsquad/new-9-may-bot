# PostgreSQL setup

1. Install PostgreSQL 15+ on your VPS or use the Docker Compose service defined in `docker-compose.yml`.
2. Create a dedicated database user and database:

```sql
CREATE USER tg_bot WITH PASSWORD 'strongpassword';
CREATE DATABASE tg_bot OWNER tg_bot;
```

3. Apply schema once (creates tables + indexes):

```bash
psql "$POSTGRES_DSN" -f database/schema.sql
```

4. Point `POSTGRES_DSN` at your asyncpg-compatible URL:

```
postgresql://tg_bot:strongpassword@127.0.0.1:5432/tg_bot
```

5. Tune `POSTGRES_POOL_MIN` / `POSTGRES_POOL_MAX` in `.env` for CPU/RAM.

For horizontal scaling, prefer a managed PostgreSQL instance with connection limits aligned to `(instances * postgres_pool_max)`.
