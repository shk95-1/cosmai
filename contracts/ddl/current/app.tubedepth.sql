--
-- PostgreSQL database dump
--

\restrict sJhIg6q8y3GHHUqUOOc86375NoXms2JGZf0hvW46p3QKZqdZP8h0RN1EgnPWhjU

-- Dumped from database version 18.6 (Debian 18.6-1.pgdg13+2)
-- Dumped by pg_dump version 18.6 (Debian 18.6-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: tubedepth; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA tubedepth;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: api_keys; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.api_keys (
    identifier character varying(32) NOT NULL,
    label character varying(100) NOT NULL,
    key_prefix character varying(12) NOT NULL,
    key_hash character varying(64) NOT NULL,
    requests_per_minute integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    last_used_at timestamp with time zone,
    revoked_at timestamp with time zone
);


--
-- Name: artifacts; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.artifacts (
    identifier character varying(32) NOT NULL,
    kind character varying(64) NOT NULL,
    target character varying(500) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    digest character varying(64) NOT NULL,
    byte_count integer NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    fresh_until timestamp with time zone NOT NULL,
    schema_version character varying(16)
);


--
-- Name: channel_snapshots; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.channel_snapshots (
    artifact_id character varying(32) NOT NULL,
    channel_id character varying(500) NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    name text,
    handle character varying(500),
    subscriber_count_approximate bigint,
    view_count bigint,
    video_count integer,
    country character varying(100)
);


--
-- Name: comments; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.comments (
    video_id character varying(500) NOT NULL,
    comment_id character varying(200) NOT NULL,
    parent_id character varying(200),
    text text NOT NULL,
    author text,
    author_id character varying(500),
    like_count bigint,
    is_hearted_by_uploader boolean NOT NULL,
    is_pinned boolean NOT NULL,
    published_at timestamp with time zone,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL
);


--
-- Name: flatten_progress; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.flatten_progress (
    identifier character varying(32) NOT NULL,
    cursor_fetched_at timestamp with time zone NOT NULL,
    cursor_identifier character varying(32) NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: jobs; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.jobs (
    identifier character varying(32) NOT NULL,
    kind character varying(64) NOT NULL,
    target character varying(500) NOT NULL,
    follow_up_kind character varying(64),
    api_key_id character varying(32),
    state character varying(9) NOT NULL,
    attempt_count integer NOT NULL,
    max_attempts integer NOT NULL,
    scheduled_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    claimed_by character varying(64),
    lease_expires_at timestamp with time zone,
    finished_at timestamp with time zone,
    cancel_requested_at timestamp with time zone,
    webhook_url character varying(500),
    webhook_attempts integer NOT NULL,
    webhook_delivered_at timestamp with time zone,
    payload_digest character varying(64),
    payload_bytes integer,
    error_code character varying(64),
    error_message text,
    refresh boolean DEFAULT false NOT NULL
);


--
-- Name: lane_health; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.lane_health (
    egress character varying(64) NOT NULL,
    lane character varying(32) NOT NULL,
    "window" double precision NOT NULL,
    in_flight integer NOT NULL,
    quarantine_streak integer NOT NULL,
    quarantined_until timestamp with time zone,
    observed_at timestamp with time zone NOT NULL
);


--
-- Name: listing_entries; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.listing_entries (
    artifact_id character varying(32) NOT NULL,
    "position" integer NOT NULL,
    kind character varying(64) NOT NULL,
    target character varying(500) NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    video_id character varying(500) NOT NULL,
    title text,
    view_count bigint,
    duration_seconds integer,
    channel text,
    channel_id character varying(500),
    published_at timestamp with time zone
);


--
-- Name: source_health; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.source_health (
    kind character varying(64) NOT NULL,
    consecutive_failures integer NOT NULL,
    blocked boolean NOT NULL,
    last_success_at timestamp with time zone,
    last_failure_at timestamp with time zone,
    last_error_code character varying(64),
    last_error_message text
);


--
-- Name: transcripts; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.transcripts (
    video_id character varying(500) NOT NULL,
    language character varying(64) NOT NULL,
    is_automatic boolean NOT NULL,
    full_text text NOT NULL,
    segment_count integer NOT NULL,
    fetched_at timestamp with time zone NOT NULL
);


--
-- Name: video_snapshots; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.video_snapshots (
    artifact_id character varying(32) NOT NULL,
    video_id character varying(500) NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    title text NOT NULL,
    channel text,
    channel_id character varying(500),
    duration_seconds integer,
    view_count bigint,
    like_count bigint,
    comment_count bigint,
    published_at timestamp with time zone,
    published_date date
);


--
-- Name: worker_control; Type: TABLE; Schema: tubedepth; Owner: -
--

CREATE TABLE tubedepth.worker_control (
    identifier character varying(32) NOT NULL,
    paused boolean NOT NULL,
    reason character varying(200),
    changed_at timestamp with time zone NOT NULL
);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (identifier);


--
-- Name: artifacts artifacts_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (identifier);


--
-- Name: channel_snapshots channel_snapshots_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.channel_snapshots
    ADD CONSTRAINT channel_snapshots_pkey PRIMARY KEY (artifact_id);


--
-- Name: comments comments_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.comments
    ADD CONSTRAINT comments_pkey PRIMARY KEY (video_id, comment_id);


--
-- Name: flatten_progress flatten_progress_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.flatten_progress
    ADD CONSTRAINT flatten_progress_pkey PRIMARY KEY (identifier);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (identifier);


--
-- Name: lane_health lane_health_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.lane_health
    ADD CONSTRAINT lane_health_pkey PRIMARY KEY (egress, lane);


--
-- Name: listing_entries listing_entries_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.listing_entries
    ADD CONSTRAINT listing_entries_pkey PRIMARY KEY (artifact_id, "position");


--
-- Name: source_health source_health_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.source_health
    ADD CONSTRAINT source_health_pkey PRIMARY KEY (kind);


--
-- Name: transcripts transcripts_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.transcripts
    ADD CONSTRAINT transcripts_pkey PRIMARY KEY (video_id, language);


--
-- Name: video_snapshots video_snapshots_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.video_snapshots
    ADD CONSTRAINT video_snapshots_pkey PRIMARY KEY (artifact_id);


--
-- Name: worker_control worker_control_pkey; Type: CONSTRAINT; Schema: tubedepth; Owner: -
--

ALTER TABLE ONLY tubedepth.worker_control
    ADD CONSTRAINT worker_control_pkey PRIMARY KEY (identifier);


--
-- Name: ix_api_keys_key_prefix; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_api_keys_key_prefix ON tubedepth.api_keys USING btree (key_prefix);


--
-- Name: ix_artifact_lookup; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_artifact_lookup ON tubedepth.artifacts USING btree (fingerprint, fresh_until);


--
-- Name: ix_artifact_recent; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_artifact_recent ON tubedepth.artifacts USING btree (kind, fetched_at);


--
-- Name: ix_artifact_target; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_artifact_target ON tubedepth.artifacts USING btree (target, fetched_at);


--
-- Name: ix_channel_snapshot_series; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_channel_snapshot_series ON tubedepth.channel_snapshots USING btree (channel_id, fetched_at);


--
-- Name: ix_comment_published; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_comment_published ON tubedepth.comments USING btree (video_id, published_at);


--
-- Name: ix_job_claimable; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_job_claimable ON tubedepth.jobs USING btree (state, scheduled_at, created_at);


--
-- Name: ix_job_lease; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_job_lease ON tubedepth.jobs USING btree (state, lease_expires_at);


--
-- Name: ix_job_recent; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_job_recent ON tubedepth.jobs USING btree (kind, created_at);


--
-- Name: ix_listing_entry_series; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_listing_entry_series ON tubedepth.listing_entries USING btree (target, fetched_at);


--
-- Name: ix_listing_entry_video; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_listing_entry_video ON tubedepth.listing_entries USING btree (video_id);


--
-- Name: ix_video_snapshot_series; Type: INDEX; Schema: tubedepth; Owner: -
--

CREATE INDEX ix_video_snapshot_series ON tubedepth.video_snapshots USING btree (video_id, fetched_at);


--
-- PostgreSQL database dump complete
--

\unrestrict sJhIg6q8y3GHHUqUOOc86375NoXms2JGZf0hvW46p3QKZqdZP8h0RN1EgnPWhjU

