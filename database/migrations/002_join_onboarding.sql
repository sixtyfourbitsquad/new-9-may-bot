-- Join-request stats, auto-approve, onboarding drip (PG-backed jobs)

ALTER TABLE channel_settings
    ADD COLUMN IF NOT EXISTS auto_approve_join_requests BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS join_requests_total BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS onboarding_messages (
    step_order      INT PRIMARY KEY CHECK (step_order >= 1 AND step_order <= 20),
    delay_seconds   INT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'
);

-- Default 1h / 1d / 3d offsets from onboarding anchor (empty payloads until admin configures)
INSERT INTO onboarding_messages (step_order, delay_seconds, payload)
VALUES
    (1, 3600, '{}'::jsonb),
    (2, 86400, '{}'::jsonb),
    (3, 259200, '{}'::jsonb)
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
