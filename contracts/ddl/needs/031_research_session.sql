-- Research state belongs to sessions; maintained-development plans remain in issues (#357).
CREATE TABLE needs.research_session (
    id text PRIMARY KEY,
    revision bigint NOT NULL DEFAULT 0,
    state jsonb NOT NULL,
    lease_token bigint NOT NULL DEFAULT 0,
    lease_owner text,
    lease_until timestamptz,
    deadline timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE needs.research_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id text NOT NULL REFERENCES needs.research_session(id),
    revision bigint NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE needs.research_action (
    id text PRIMARY KEY,
    session_id text NOT NULL REFERENCES needs.research_session(id),
    request_id text NOT NULL,
    kind text NOT NULL,
    input_digest text NOT NULL,
    observed_revision bigint NOT NULL,
    lease_token bigint NOT NULL,
    status text NOT NULL CHECK (status IN ('reserved', 'done', 'failed', 'uncertain', 'stale')),
    attempts integer NOT NULL DEFAULT 1,
    request jsonb NOT NULL,
    result jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, request_id, kind)
);
GRANT SELECT, INSERT, UPDATE ON needs.research_session, needs.research_action TO needs_runtime;
GRANT SELECT, INSERT ON needs.research_event TO needs_runtime;
GRANT USAGE, SELECT ON SEQUENCE needs.research_event_id_seq TO needs_runtime;
