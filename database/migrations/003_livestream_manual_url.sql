-- Optional HTTPS/join URL for private channels (livestream alerts).
ALTER TABLE livestream_settings
    ADD COLUMN IF NOT EXISTS manual_live_url TEXT;
