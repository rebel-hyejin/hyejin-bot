-- hyejin-bot — schema_version=7.
-- Adds the generic daily-cron trigger's fire-once-per-day state. Feature 003
-- (news clip). One row per cron job name; `last_fired_date` is the local-tz
-- calendar date (YYYY-MM-DD) of the most recent emit, so a same-day restart
-- of the daemon does not re-fire a job that already ran today.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cron_state (
    job_name         TEXT NOT NULL PRIMARY KEY,  -- e.g. "news_daily"
    last_fired_date  TEXT NOT NULL,              -- local-tz calendar date YYYY-MM-DD
    last_fired_at    TEXT NOT NULL               -- ISO8601 UTC of the emit
);

UPDATE meta SET value = '7' WHERE key = 'schema_version';
