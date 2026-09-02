-- SoberLink Phase 1 schema (PostgreSQL, local dev)
-- Option B risk model: non-overlapping factors (3h volume, velocity flag, venues, refusal, staff concern)
-- No directly identifying patron data is stored anywhere in this schema.

CREATE TABLE venues (
    venue_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_name      TEXT NOT NULL,
    venue_code      TEXT UNIQUE NOT NULL,   -- human-friendly label e.g. VEN-001, not used as a key
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE venue_staff (
    staff_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_id        UUID NOT NULL REFERENCES venues(venue_id),
    staff_login     TEXT UNIQUE NOT NULL,   -- prototype only: real login/RBAC comes in Phase 11
    role            TEXT NOT NULL DEFAULT 'rsa_staff' CHECK (role IN ('rsa_staff','venue_admin')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    session_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash      TEXT UNIQUE NOT NULL,   -- SHA-256 hex digest of the patron's token; raw token never stored
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,   -- e.g. created_at + 8 hours, enforced by app logic
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','checked_out')),
    -- cached/derived fields, recomputed on each risk calculation (not the source of truth on their own)
    standard_drinks_3h  NUMERIC(4,1) NOT NULL DEFAULT 0,
    rapid_flag          BOOLEAN NOT NULL DEFAULT false,
    previous_refusal    BOOLEAN NOT NULL DEFAULT false,
    staff_concern_flag  BOOLEAN NOT NULL DEFAULT false,
    risk_score          INT NOT NULL DEFAULT 0,
    risk_level          TEXT NOT NULL DEFAULT 'LOW' CHECK (risk_level IN ('LOW','MODERATE','HIGH','VERY HIGH'))
);

CREATE TABLE drink_events (
    drink_event_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(session_id),
    venue_id        UUID NOT NULL REFERENCES venues(venue_id),
    drink_type      TEXT NOT NULL,          -- e.g. 'beer_375ml', 'wine_150ml', 'spirit_30ml'
    volume_ml       NUMERIC(6,1) NOT NULL,
    abv             NUMERIC(4,2) NOT NULL,  -- e.g. 4.80 for 4.8%
    standard_drinks NUMERIC(4,2) NOT NULL,  -- volume_ml * abv/100 * 0.789 / 10, computed in app layer
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE venue_events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(session_id),
    venue_id        UUID NOT NULL REFERENCES venues(venue_id),
    event_type      TEXT NOT NULL CHECK (event_type IN
                        ('CHECK_IN','DRINK_RECORDED','STAFF_CONCERN','SERVICE_REFUSAL','CHECK_OUT')),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE staff_events (
    staff_event_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(session_id),
    staff_id        UUID NOT NULL REFERENCES venue_staff(staff_id),
    venue_id        UUID NOT NULL REFERENCES venues(venue_id),
    event_type      TEXT NOT NULL CHECK (event_type IN ('STAFF_CONCERN','SERVICE_REFUSAL')),
    note            TEXT,                   -- optional free-text, staff-entered, never patron-entered
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for the lookups the risk engine will do constantly
CREATE INDEX idx_drink_events_session_time ON drink_events (session_id, recorded_at);
CREATE INDEX idx_venue_events_session ON venue_events (session_id);
CREATE INDEX idx_sessions_token_hash ON sessions (token_hash);
CREATE INDEX idx_sessions_expires_at ON sessions (expires_at);