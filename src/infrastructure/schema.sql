PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS parcel (
 id INTEGER PRIMARY KEY,
 tracking_no TEXT NOT NULL UNIQUE,
 recipient TEXT NOT NULL,
 location TEXT NOT NULL,
 operator TEXT NOT NULL,
 request_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('stored','exception','picked_up','returned')),
 phone TEXT NOT NULL DEFAULT '',
 carrier TEXT NOT NULL DEFAULT '',
 pickup_code_hash TEXT NOT NULL DEFAULT '',
 failed_pickups INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(operator,request_key)
);
CREATE INDEX IF NOT EXISTS idx_parcel_status_created ON parcel(status,created_at);
CREATE INDEX IF NOT EXISTS idx_parcel_recipient ON parcel(recipient);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, parcel_id INTEGER NOT NULL REFERENCES parcel(id),
 action TEXT NOT NULL, actor TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_parcel ON audit(parcel_id,id);
CREATE TABLE IF NOT EXISTS parcel_exception (
 id INTEGER PRIMARY KEY, parcel_id INTEGER NOT NULL REFERENCES parcel(id),
 kind TEXT NOT NULL, note TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('open','resolved')),
 resolution TEXT, resolution_note TEXT, created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_exception ON parcel_exception(parcel_id) WHERE status='open';
CREATE TABLE IF NOT EXISTS notification_outbox (
 id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
 parcel_id INTEGER NOT NULL REFERENCES parcel(id), payload TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','processing','sent','failed','dead')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
 claim_token TEXT, lease_until TEXT, last_error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_ready ON notification_outbox(status,next_attempt_at);
CREATE TABLE IF NOT EXISTS station_user (
 username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','manager','clerk','auditor')),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
 token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES station_user(username),
 expires_at TEXT NOT NULL
);
