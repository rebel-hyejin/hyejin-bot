-- daeyeon-bot — schema_version=2.
-- Adds the GitHub PR-review-bot's per-PR polling state and per-review audit log.
PRAGMA foreign_keys = ON;

-- Per-PR state the polling trigger maintains so it can recognize re-requests
-- at the same head SHA. One row per (repo, pr_number).
CREATE TABLE IF NOT EXISTS gh_review_requested_state (
    repo            TEXT NOT NULL,           -- "owner/repo"
    pr_number       INTEGER NOT NULL,
    head_sha        TEXT NOT NULL,           -- last-observed head commit SHA
    request_gen     INTEGER NOT NULL,        -- monotonic; increments on re-request or SHA change
    in_pending_set  INTEGER NOT NULL,        -- 0 / 1; was the PR in the last poll's review-requested:@me result?
    last_observed_at TEXT NOT NULL,          -- ISO8601 UTC
    PRIMARY KEY (repo, pr_number)
);
CREATE INDEX IF NOT EXISTS idx_grrs_pending ON gh_review_requested_state(in_pending_set);

-- Per-review audit row. One row per posted (or skipped) review attempt; on
-- force-supersede the row is updated and the prior review_id pushed onto the
-- superseded_review_ids JSON array.
CREATE TABLE IF NOT EXISTS pr_review_audit (
    id                       INTEGER PRIMARY KEY,
    event_id                 TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    repo                     TEXT NOT NULL,
    pr_number                INTEGER NOT NULL,
    head_sha                 TEXT NOT NULL,
    request_gen              TEXT NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN
                                 ('posted',
                                  'skipped_self_authored',
                                  'skipped_withdrawn',
                                  'skipped_too_large',
                                  'skipped_already_reviewed',
                                  'failed')),
    review_id                INTEGER,
    submitted_at             TEXT,
    summary_chars            INTEGER,
    inline_comment_count     INTEGER,
    superseded_review_ids    TEXT NOT NULL DEFAULT '[]',
    persona_skill            TEXT,
    persona_mtime_ns         INTEGER,
    error                    TEXT,
    created_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pra_repo_pr_sha
    ON pr_review_audit(repo, pr_number, head_sha);
CREATE INDEX IF NOT EXISTS idx_pra_event ON pr_review_audit(event_id);

UPDATE meta SET value = '2' WHERE key = 'schema_version';
