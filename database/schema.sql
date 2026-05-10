-- PostgreSQL schema for Community Broadcast Bot
-- Run once on empty database (see deploy/POSTGRESQL.md)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- users: collected subscribers / chatters
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id           BIGINT PRIMARY KEY,
    username          TEXT,
    first_name        TEXT,
    language_code     TEXT,
    join_date         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    source_channel    TEXT,
    broadcast_status  TEXT NOT NULL DEFAULT 'active',
    total_messages    BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_users_broadcast_status ON users (broadcast_status);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users (is_active) WHERE is_active = TRUE;

-- ---------------------------------------------------------------------------
-- admins
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admins (
    admin_id   BIGINT PRIMARY KEY,
    role       TEXT NOT NULL CHECK (role IN ('owner', 'moderator', 'support')),
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by   BIGINT
);

CREATE INDEX IF NOT EXISTS idx_admins_role ON admins (role);

-- ---------------------------------------------------------------------------
-- broadcasts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS broadcasts (
    id                BIGSERIAL PRIMARY KEY,
    created_by        BIGINT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'draft',
    payload           JSONB NOT NULL DEFAULT '{}',
    scheduled_at      TIMESTAMPTZ,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    total_targets     BIGINT NOT NULL DEFAULT 0,
    delivered_count   BIGINT NOT NULL DEFAULT 0,
    failed_count      BIGINT NOT NULL DEFAULT 0,
    blocked_count     BIGINT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts (status);
CREATE INDEX IF NOT EXISTS idx_broadcasts_created_at ON broadcasts (created_at DESC);

-- ---------------------------------------------------------------------------
-- broadcast_logs (per-recipient outcome)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS broadcast_logs (
    id             BIGSERIAL PRIMARY KEY,
    broadcast_id   BIGINT NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    user_id        BIGINT NOT NULL,
    status         TEXT NOT NULL,
    error_code     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_broadcast_logs_broadcast ON broadcast_logs (broadcast_id);
CREATE INDEX IF NOT EXISTS idx_broadcast_logs_user ON broadcast_logs (user_id);

-- ---------------------------------------------------------------------------
-- scheduled_jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id             BIGSERIAL PRIMARY KEY,
    created_by     BIGINT NOT NULL,
    run_at         TIMESTAMPTZ NOT NULL,
    payload        JSONB NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_run ON scheduled_jobs (run_at) WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- welcome_messages (multi-step)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS welcome_messages (
    id          BIGSERIAL PRIMARY KEY,
    step_order  INT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    UNIQUE (step_order)
);

-- ---------------------------------------------------------------------------
-- retention_messages
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retention_messages (
    id              BIGSERIAL PRIMARY KEY,
    step_order      INT NOT NULL,
    delay_seconds   INT NOT NULL DEFAULT 3600,
    payload         JSONB NOT NULL DEFAULT '{}',
    UNIQUE (step_order)
);

-- ---------------------------------------------------------------------------
-- livestream_settings (singleton row id=1)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS livestream_settings (
    id                      SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    cooldown_seconds        INT NOT NULL DEFAULT 300,
    notification_template   TEXT NOT NULL DEFAULT '🔴 LIVE STREAM STARTED! Join now!',
    banner_payload          JSONB,
    button_payload          JSONB,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO livestream_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- channel_settings (monitored chat + retention toggles)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channel_settings (
    id                          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    monitored_chat_id           BIGINT,
    retention_enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    auto_approve_join_requests  BOOLEAN NOT NULL DEFAULT FALSE,
    join_requests_total         BIGINT NOT NULL DEFAULT 0,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO channel_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- inline_buttons (saved keyboard presets)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inline_buttons (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    buttons     JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- system_logs (audit)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_logs (
    id          BIGSERIAL PRIMARY KEY,
    level       TEXT NOT NULL,
    source      TEXT NOT NULL,
    message     TEXT NOT NULL,
    context     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs (created_at DESC);

-- ---------------------------------------------------------------------------
-- user_activity
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_activity (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    action      TEXT NOT NULL,
    meta        JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_user ON user_activity (user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_created ON user_activity (created_at DESC);

-- ---------------------------------------------------------------------------
-- onboarding_messages + onboarding_drip_jobs (post-/start drip, PG-backed)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_messages (
    step_order      INT PRIMARY KEY CHECK (step_order >= 1 AND step_order <= 20),
    delay_seconds   INT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'
);

INSERT INTO onboarding_messages (step_order, delay_seconds, payload) VALUES
    (1, 3600, '{}'),
    (2, 86400, '{}'),
    (3, 259200, '{}')
ON CONFLICT (step_order) DO NOTHING;

CREATE TABLE IF NOT EXISTS onboarding_drip_jobs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    step_order      INT NOT NULL,
    fire_at         TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    UNIQUE (user_id, step_order)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_drip_fire_pending
    ON onboarding_drip_jobs (fire_at)
    WHERE sent_at IS NULL;
