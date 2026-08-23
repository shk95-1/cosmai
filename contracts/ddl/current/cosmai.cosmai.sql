--
-- PostgreSQL database dump
--

\restrict JlGdytIzumCb2uLz9jld0nlgIw8OMOxT8HsgUBg1FRLxRyvmwY3ZAbUPuMyInWu

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
-- Name: cosmai; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA cosmai;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: job; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.job (
    id uuid NOT NULL,
    handler text NOT NULL,
    payload jsonb NOT NULL,
    state text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    terminal_reason text,
    correlation_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT job_attempts_stay_within_budget CHECK (((attempt_count >= 0) AND (attempt_count <= max_attempts))),
    CONSTRAINT job_budget_is_at_least_one CHECK ((max_attempts >= 1)),
    CONSTRAINT job_lease_expiry_is_set_exactly_while_running CHECK (((state = 'RUNNING'::text) = (lease_expires_at IS NOT NULL))),
    CONSTRAINT job_lease_is_held_exactly_while_running CHECK (((state = 'RUNNING'::text) = (lease_owner IS NOT NULL))),
    CONSTRAINT job_state_is_known CHECK ((state = ANY (ARRAY['PENDING'::text, 'RUNNING'::text, 'SUCCEEDED'::text, 'FAILED'::text]))),
    CONSTRAINT job_terminal_reason_belongs_to_failure CHECK (((terminal_reason IS NULL) OR (state = 'FAILED'::text)))
);


--
-- Name: job_attempt; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.job_attempt (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    attempt_no integer NOT NULL,
    worker_id text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    outcome text,
    error_class text,
    error_summary text,
    error_detail jsonb,
    correlation_id text NOT NULL,
    CONSTRAINT job_attempt_closes_with_an_outcome CHECK (((finished_at IS NULL) = (outcome IS NULL))),
    CONSTRAINT job_attempt_number_is_one_based CHECK ((attempt_no >= 1)),
    CONSTRAINT job_attempt_outcome_is_known CHECK (((outcome IS NULL) OR (outcome = ANY (ARRAY['SUCCEEDED'::text, 'RETRYABLE_FAILURE'::text, 'PERMANENT_FAILURE'::text, 'ABANDONED'::text]))))
);


--
-- Name: normalized_result; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.normalized_result (
    id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    source_id text NOT NULL,
    addon_id text NOT NULL,
    addon_version text NOT NULL,
    output_contract_version text NOT NULL,
    source_item_key text NOT NULL,
    body jsonb NOT NULL,
    body_sha256 text NOT NULL,
    notes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT normalized_result_digest_is_a_sha256 CHECK ((body_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: platform_effect; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.platform_effect (
    effect_key text NOT NULL,
    job_id uuid NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    payload jsonb
);


--
-- Name: raw_envelope; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.raw_envelope (
    id uuid NOT NULL,
    source_id text NOT NULL,
    job_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    addon_id text NOT NULL,
    addon_version text NOT NULL,
    endpoint_ref text,
    input_ref text,
    request_summary jsonb,
    status integer,
    response_headers jsonb,
    body bytea NOT NULL,
    body_sha256 text NOT NULL,
    content_type text,
    retrieved_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT raw_envelope_digest_is_a_sha256 CHECK ((body_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT raw_envelope_names_one_origin CHECK (((endpoint_ref IS NULL) <> (input_ref IS NULL)))
);


--
-- Name: raw_item; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.raw_item (
    id uuid NOT NULL,
    envelope_id uuid NOT NULL,
    source_id text NOT NULL,
    item_key text NOT NULL,
    payload bytea NOT NULL,
    content_type text NOT NULL,
    notes jsonb DEFAULT '{}'::jsonb NOT NULL,
    emitted_at timestamp with time zone DEFAULT now() NOT NULL,
    seq bigint NOT NULL,
    payload_sha256 text GENERATED ALWAYS AS (encode(sha256(payload), 'hex'::text)) STORED NOT NULL,
    CONSTRAINT raw_item_digest_is_a_sha256 CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: raw_item_seq_seq; Type: SEQUENCE; Schema: cosmai; Owner: -
--

ALTER TABLE cosmai.raw_item ALTER COLUMN seq ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME cosmai.raw_item_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schedule; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.schedule (
    source_id text NOT NULL,
    interval_seconds integer NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    next_run_at timestamp with time zone,
    last_run_at timestamp with time zone,
    CONSTRAINT schedule_interval_is_positive CHECK ((interval_seconds > 0))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.schema_migrations (
    version text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: snapshot; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.snapshot (
    id uuid NOT NULL,
    source_id text NOT NULL,
    item_count integer NOT NULL,
    manifest_sha256 text NOT NULL,
    selection jsonb DEFAULT '{}'::jsonb NOT NULL,
    sealed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT snapshot_item_count_is_not_negative CHECK ((item_count >= 0)),
    CONSTRAINT snapshot_manifest_digest_is_a_sha256 CHECK ((manifest_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: snapshot_item; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.snapshot_item (
    snapshot_id uuid NOT NULL,
    ordinal integer NOT NULL,
    item_key text NOT NULL,
    payload bytea NOT NULL,
    content_type text NOT NULL,
    payload_sha256 text NOT NULL,
    CONSTRAINT snapshot_item_digest_is_a_sha256 CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT snapshot_item_ordinal_is_zero_based CHECK ((ordinal >= 0))
);


--
-- Name: source; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.source (
    source_id text NOT NULL,
    addon_id text NOT NULL,
    addon_version text NOT NULL,
    kind text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    config_schema_version text NOT NULL,
    credential_ref text,
    outbound_profile jsonb,
    input_profile jsonb,
    data_class text DEFAULT 'local'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_an_importer_is_granted_no_outbound_profile CHECK (((kind <> 'importer'::text) OR (outbound_profile IS NULL))),
    CONSTRAINT source_credential_ref_is_a_key_name CHECK (((credential_ref IS NULL) OR (credential_ref ~ '^COSMA_SRC_[A-Z0-9_]+$'::text))),
    CONSTRAINT source_data_class_is_known CHECK ((data_class = ANY (ARRAY['public'::text, 'local'::text, 'private'::text]))),
    CONSTRAINT source_kind_is_known CHECK ((kind = ANY (ARRAY['collector'::text, 'importer'::text, 'normalizer'::text]))),
    CONSTRAINT source_normalizer_reaches_nothing_outside_its_snapshot CHECK (((kind <> 'normalizer'::text) OR ((outbound_profile IS NULL) AND (credential_ref IS NULL)))),
    CONSTRAINT source_only_an_importer_reads_a_local_input CHECK (((kind = 'importer'::text) OR (input_profile IS NULL)))
);


--
-- Name: source_cursor; Type: TABLE; Schema: cosmai; Owner: -
--

CREATE TABLE cosmai.source_cursor (
    source_id text NOT NULL,
    stream text NOT NULL,
    cursor jsonb NOT NULL,
    updated_by_attempt uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: job_attempt job_attempt_number_is_unique_per_job; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.job_attempt
    ADD CONSTRAINT job_attempt_number_is_unique_per_job UNIQUE (job_id, attempt_no);


--
-- Name: job_attempt job_attempt_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.job_attempt
    ADD CONSTRAINT job_attempt_pkey PRIMARY KEY (id);


--
-- Name: job job_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.job
    ADD CONSTRAINT job_pkey PRIMARY KEY (id);


--
-- Name: normalized_result normalized_result_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.normalized_result
    ADD CONSTRAINT normalized_result_pkey PRIMARY KEY (id);


--
-- Name: platform_effect platform_effect_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.platform_effect
    ADD CONSTRAINT platform_effect_pkey PRIMARY KEY (effect_key);


--
-- Name: raw_envelope raw_envelope_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.raw_envelope
    ADD CONSTRAINT raw_envelope_pkey PRIMARY KEY (id);


--
-- Name: raw_item raw_item_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.raw_item
    ADD CONSTRAINT raw_item_pkey PRIMARY KEY (id);


--
-- Name: schedule schedule_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.schedule
    ADD CONSTRAINT schedule_pkey PRIMARY KEY (source_id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: snapshot_item snapshot_item_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.snapshot_item
    ADD CONSTRAINT snapshot_item_pkey PRIMARY KEY (snapshot_id, ordinal);


--
-- Name: snapshot snapshot_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.snapshot
    ADD CONSTRAINT snapshot_pkey PRIMARY KEY (id);


--
-- Name: source_cursor source_cursor_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.source_cursor
    ADD CONSTRAINT source_cursor_pkey PRIMARY KEY (source_id, stream);


--
-- Name: source source_pkey; Type: CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.source
    ADD CONSTRAINT source_pkey PRIMARY KEY (source_id);


--
-- Name: job_attempt_one_open_per_job; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE UNIQUE INDEX job_attempt_one_open_per_job ON cosmai.job_attempt USING btree (job_id) WHERE (finished_at IS NULL);


--
-- Name: job_due_while_pending; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX job_due_while_pending ON cosmai.job USING btree (available_at) WHERE (state = 'PENDING'::text);


--
-- Name: job_lease_deadline_while_running; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX job_lease_deadline_while_running ON cosmai.job USING btree (lease_expires_at) WHERE (state = 'RUNNING'::text);


--
-- Name: normalized_result_by_snapshot; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX normalized_result_by_snapshot ON cosmai.normalized_result USING btree (snapshot_id);


--
-- Name: normalized_result_by_source; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX normalized_result_by_source ON cosmai.normalized_result USING btree (source_id, created_at);


--
-- Name: normalized_result_one_per_run_and_item; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE UNIQUE INDEX normalized_result_one_per_run_and_item ON cosmai.normalized_result USING btree (snapshot_id, addon_id, addon_version, output_contract_version, source_item_key);


--
-- Name: platform_effect_by_job; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX platform_effect_by_job ON cosmai.platform_effect USING btree (job_id);


--
-- Name: raw_envelope_by_attempt; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX raw_envelope_by_attempt ON cosmai.raw_envelope USING btree (attempt_id);


--
-- Name: raw_envelope_by_source; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX raw_envelope_by_source ON cosmai.raw_envelope USING btree (source_id, retrieved_at);


--
-- Name: raw_item_by_envelope; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX raw_item_by_envelope ON cosmai.raw_item USING btree (envelope_id);


--
-- Name: raw_item_by_source_key; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX raw_item_by_source_key ON cosmai.raw_item USING btree (source_id, item_key, seq DESC);


--
-- Name: snapshot_item_one_per_key; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE UNIQUE INDEX snapshot_item_one_per_key ON cosmai.snapshot_item USING btree (snapshot_id, item_key);


--
-- Name: snapshot_sealed; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX snapshot_sealed ON cosmai.snapshot USING btree (source_id, sealed_at) WHERE (sealed_at IS NOT NULL);


--
-- Name: source_by_addon; Type: INDEX; Schema: cosmai; Owner: -
--

CREATE INDEX source_by_addon ON cosmai.source USING btree (addon_id);


--
-- Name: job_attempt job_attempt_job_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.job_attempt
    ADD CONSTRAINT job_attempt_job_id_fkey FOREIGN KEY (job_id) REFERENCES cosmai.job(id);


--
-- Name: normalized_result normalized_result_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.normalized_result
    ADD CONSTRAINT normalized_result_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES cosmai.snapshot(id);


--
-- Name: normalized_result normalized_result_source_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.normalized_result
    ADD CONSTRAINT normalized_result_source_id_fkey FOREIGN KEY (source_id) REFERENCES cosmai.source(source_id);


--
-- Name: platform_effect platform_effect_job_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.platform_effect
    ADD CONSTRAINT platform_effect_job_id_fkey FOREIGN KEY (job_id) REFERENCES cosmai.job(id);


--
-- Name: raw_envelope raw_envelope_attempt_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.raw_envelope
    ADD CONSTRAINT raw_envelope_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES cosmai.job_attempt(id);


--
-- Name: raw_envelope raw_envelope_job_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.raw_envelope
    ADD CONSTRAINT raw_envelope_job_id_fkey FOREIGN KEY (job_id) REFERENCES cosmai.job(id);


--
-- Name: raw_envelope raw_envelope_source_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.raw_envelope
    ADD CONSTRAINT raw_envelope_source_id_fkey FOREIGN KEY (source_id) REFERENCES cosmai.source(source_id);


--
-- Name: raw_item raw_item_envelope_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.raw_item
    ADD CONSTRAINT raw_item_envelope_id_fkey FOREIGN KEY (envelope_id) REFERENCES cosmai.raw_envelope(id);


--
-- Name: raw_item raw_item_source_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.raw_item
    ADD CONSTRAINT raw_item_source_id_fkey FOREIGN KEY (source_id) REFERENCES cosmai.source(source_id);


--
-- Name: schedule schedule_source_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.schedule
    ADD CONSTRAINT schedule_source_id_fkey FOREIGN KEY (source_id) REFERENCES cosmai.source(source_id);


--
-- Name: snapshot_item snapshot_item_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.snapshot_item
    ADD CONSTRAINT snapshot_item_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES cosmai.snapshot(id);


--
-- Name: snapshot snapshot_source_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.snapshot
    ADD CONSTRAINT snapshot_source_id_fkey FOREIGN KEY (source_id) REFERENCES cosmai.source(source_id);


--
-- Name: source_cursor source_cursor_source_id_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.source_cursor
    ADD CONSTRAINT source_cursor_source_id_fkey FOREIGN KEY (source_id) REFERENCES cosmai.source(source_id);


--
-- Name: source_cursor source_cursor_updated_by_attempt_fkey; Type: FK CONSTRAINT; Schema: cosmai; Owner: -
--

ALTER TABLE ONLY cosmai.source_cursor
    ADD CONSTRAINT source_cursor_updated_by_attempt_fkey FOREIGN KEY (updated_by_attempt) REFERENCES cosmai.job_attempt(id);


--
-- PostgreSQL database dump complete
--

\unrestrict JlGdytIzumCb2uLz9jld0nlgIw8OMOxT8HsgUBg1FRLxRyvmwY3ZAbUPuMyInWu

