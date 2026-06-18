-- daeyeon-bot initial schema (PLAN.md §4.1).
-- This file is the source of truth for schema_version=1.
-- Any change to schema must be a NEW migration file (002_*.sql, 003_*.sql, …).

PRAGMA foreign_keys = ON;

-- meta: self-versioning migration tracker
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1');

-- events: every emitted event (90-day retention)
CREATE TABLE IF NOT EXISTS events (
    id                TEXT PRIMARY KEY,           -- UUIDv7
    type              TEXT NOT NULL,
    schema_version    INTEGER NOT NULL,
    source            TEXT NOT NULL,
    source_dedup_key  TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    trace_id          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE(source, source_dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);

-- outbox: event × handler queue with claim-row semantics
CREATE TABLE IF NOT EXISTS outbox (
    id                INTEGER PRIMARY KEY,
    event_id          TEXT NOT NULL REFERENCES events(id),
    handler           TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN
                          ('pending','running','acked','retry','dead_letter','interrupted')),
    attempt           INTEGER NOT NULL DEFAULT 0,
    attempt_epoch     INTEGER NOT NULL DEFAULT 0,
    next_attempt_at   TEXT,
    claimed_by        TEXT,
    claimed_at        TEXT,
    last_error        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(event_id, handler, attempt_epoch)
);
CREATE INDEX IF NOT EXISTS idx_outbox_status_next ON outbox(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_outbox_claimed ON outbox(claimed_by);

-- runs: handler execution audit trail
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY,
    outbox_id         INTEGER NOT NULL REFERENCES outbox(id),
    event_id          TEXT NOT NULL,
    handler           TEXT NOT NULL,
    attempt_epoch     INTEGER NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL,
    duration_ms       INTEGER,
    triggered_by      TEXT NOT NULL DEFAULT 'dispatcher',
    error             TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_handler_finished ON runs(handler, finished_at);

-- dedup_keys: idempotency
CREATE TABLE IF NOT EXISTS dedup_keys (
    key         TEXT PRIMARY KEY,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dedup_expires ON dedup_keys(expires_at);

-- ratelimit_buckets: persisted token buckets, atomic UPDATE only
CREATE TABLE IF NOT EXISTS ratelimit_buckets (
    name           TEXT PRIMARY KEY,
    tokens         REAL NOT NULL,
    capacity       REAL NOT NULL,
    refill_per_sec REAL NOT NULL,
    last_refill    TEXT NOT NULL
);

-- quarantine: noisy triggers parked here after 5 fails / 10 min
CREATE TABLE IF NOT EXISTS quarantine (
    trigger_name   TEXT PRIMARY KEY,
    quarantined_at TEXT NOT NULL,
    reason         TEXT NOT NULL
);
