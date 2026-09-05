-- User decision 2026-08-27 (#168 proposal B): removes **collected original text and the collector's
-- internal state** from anon. Aggregated facts stay -- the old stack's design was originally "anonymous
-- read on the local network", and that is the reason data-portal exists (service/data-portal/README.md:3).
--
-- **Not run yet.** db/migrate.sh only picks up two files by name (migrate.sh:100,105), so this file
-- sits outside the run path, and merely having it here does nothing on its own. Applying it happens one
-- command at a time from the coordinator session -- since this is old-stack privilege, it is under
-- STATE.md §3's every-time approval.
--
-- Compare before and after applying with: db/grants/postgrest_anon_check.sql (read-only). After
-- applying, what anon sees must be needs 11 + trend_radar 9 + tubedepth 3 = 23 (needs is untouched by
-- this file).
--
-- Run by the superuser (platform): trend_radar_owner and tubedepth_owner are the ones that granted the
-- privileges being revoked here, and the DEFAULT PRIVILEGES being erased belong to tubedepth_owner.
-- The two schemas are treated differently -- trend_radar only has its membership cut, and its default
-- privileges are left alone (see section 1's comment).

-- ---------------------------------------------------------------------------
-- 1. trend_radar -- a single role membership had all 13 open. There was no direct GRANT.
-- ---------------------------------------------------------------------------
REVOKE trend_radar_reader FROM postgrest_anon;

-- This schema's DEFAULT PRIVILEGES are **deliberately left untouched.** This is not an omission.
--   pg_default_acl's trend_radar row reads trend_radar_owner -> `trend_radar_reader=r`, not
--   postgrest_anon. Once the one line above makes anon no longer a member of that role, it stops
--   inheriting the default privileges too, so anon's drift is already stopped by that one line alone.
--   Erasing it instead would break the dashboard: before trend_radar_reader is anon's channel, it is
--   the role trend-radar-dashboard **logs into directly**
--   (service/stack/docker-compose.yml:172's TREND_RADAR_READONLY_DATABASE_URL, rolcanlogin=t), and
--   removing the default privileges would keep that screen from ever reading a table that later
--   appears in trend_radar. User decision 2 was "stop the drift without changing what is open right
--   now", not narrowing the dashboard's future access.
--   The tubedepth side (section 2 below) is a different case: its default privileges are granted to
--   postgrest_anon **directly**.

-- Schema USAGE is **granted back here.** anon had also inherited this through membership:
-- trend_radar's nspacl reads `trend_radar_reader=U/trend_radar_owner` with no postgrest_anon entry.
-- So the REVOKE line above takes USAGE along with SELECT -- even after tables are handed back by
-- name, PostgREST still returns 401 (measured in production 2026-08-27, all 9 of trend_radar's tables
-- returning 401 right after applying).
-- tubedepth and needs need no such line: both already have `postgrest_anon=U` directly in nspacl, so
-- section 2's REVOKE below never touches their USAGE. This is the **same asymmetry** as DEFAULT
-- PRIVILEGES -- the one reason this file ever grants something back on trend_radar alone.
-- Harmless even if already present (GRANT is idempotent).
GRANT USAGE ON SCHEMA trend_radar TO postgrest_anon;

-- Tables are granted by name in place of membership. From here on, these nine lines are all anon sees
-- in this schema, and opening a new table means adding one more line here.
GRANT SELECT ON
    trend_radar.product,             -- the product axis (source, product_key, name, brand, volume)
    trend_radar.rank_snapshot,       -- ranking over time
    trend_radar.price_point,         -- price and discount rate
    trend_radar.new_product,         -- newly listed products
    trend_radar.new_products_view,   -- the view over the above. data-portal renders it no differently
                                      -- from a table
    trend_radar.review_stats,        -- review count and rating distribution (aggregated, not the
                                      -- original text)
    trend_radar.review_topic,        -- topics and shares the site itself published (aggregated)
    trend_radar.review_answer,       -- daisomall's survey answers (a choice, not free-form text)
    trend_radar.review_summary       -- a summary the site itself published (not the original text)
    TO postgrest_anon;

-- Left out: review is the review's **full body** (body), 30,044 rows, and is exactly the exposure
-- #144/#168 targeted.
-- run, run_source and fetch_log are collection operations records, not data.
-- alembic_version was already closed from the start (it predates DEFAULT PRIVILEGES, an accident of
-- order rather than policy).

-- ---------------------------------------------------------------------------
-- 2. tubedepth -- there is no reader role, so this is granted to anon directly (a different shape
--    from trend_radar). All 12 tables but api_keys were open.
-- ---------------------------------------------------------------------------
REVOKE SELECT ON ALL TABLES IN SCHEMA tubedepth FROM postgrest_anon;

-- DEFAULT PRIVILEGES **must** be erased here -- this alone is the reason it is the exact opposite of
-- section 1: pg_default_acl's tubedepth row is postgrest_anon=r/tubedepth_owner, granted directly to
-- anon, so leaving it in place would attach any table the next migration creates straight to anon.
-- This is exactly the path that took this schema from 6 tables on 2026-08-21 to 12 today
-- (service/data-portal/docs/postgrest-observed.md:60). No role loses anything by erasing it: anon is
-- the only beneficiary of this default privilege, and tubedepth_runtime holds its own share separately.
ALTER DEFAULT PRIVILEGES FOR ROLE tubedepth_owner IN SCHEMA tubedepth
    REVOKE SELECT ON TABLES FROM postgrest_anon;

GRANT SELECT ON
    tubedepth.video_snapshots,       -- video metadata (title, channel_id, view_count)
    tubedepth.channel_snapshots,     -- channel metadata
    tubedepth.listing_entries        -- listings (kind, target, video_id, title)
    TO postgrest_anon;

-- Left out: comments (285,749 rows) and transcripts (5,303 rows) are collected original text.
-- jobs (337,201 rows), artifacts, worker_control, lane_health, source_health and flatten_progress are
-- the collector's internal state; alembic_version is the migration ledger.
-- api_keys has been revoked all along and is not opened here either.

-- USAGE stays with anon on all three schemas -- only the path it arrives by differs: needs and
-- tubedepth have always had it directly in nspacl, and trend_radar is the only one section 1 grants
-- back (since it vanishes along with membership).
-- "What is visible" only holds when SELECT and USAGE **both** exist: no matter how many tables are
-- granted, a schema without USAGE is the same as zero. postgrest_anon_check.sql's section 6 measures
-- that separately.
-- The 0.0.0.0 bind is not this file's concern -- it is handled separately by user decision 3
-- (2026-08-27).

NOTIFY pgrst, 'reload schema';

-- ---------------------------------------------------------------------------
-- Rollback -- restores exactly the state measured on 2026-08-27. As superuser, top to bottom.
-- Seven lines. The rule is to restore relacl/nspacl to **the same shape** as today, not merely to
-- match effective privileges -- which is why the direct grants proposal B newly made (nine tables plus
-- one schema USAGE) are withdrawn before membership is reattached: membership would hand both of them
-- back again on its own.
-- trend_radar's DEFAULT PRIVILEGES were never touched in the first place, so there is no line to
-- restore for them either (section 1's comment).
-- On the tubedepth side, ON ALL TABLES re-covers all 12 so no separate REVOKE is needed, and USAGE was
-- never granted by this file, so there is nothing to restore for it.
-- ---------------------------------------------------------------------------
--   REVOKE SELECT ON trend_radar.product, trend_radar.rank_snapshot, trend_radar.price_point,
--       trend_radar.new_product, trend_radar.new_products_view, trend_radar.review_stats,
--       trend_radar.review_topic, trend_radar.review_answer, trend_radar.review_summary
--       FROM postgrest_anon;
--   REVOKE USAGE ON SCHEMA trend_radar FROM postgrest_anon;
--   GRANT trend_radar_reader TO postgrest_anon;
--   GRANT SELECT ON ALL TABLES IN SCHEMA tubedepth TO postgrest_anon;
--   REVOKE ALL ON TABLE tubedepth.api_keys FROM postgrest_anon;
--   ALTER DEFAULT PRIVILEGES FOR ROLE tubedepth_owner IN SCHEMA tubedepth
--       GRANT SELECT ON TABLES TO postgrest_anon;
--   NOTIFY pgrst, 'reload schema';
--
-- The original source is the old stack's init (service/stack/init/20-postgrest-roles.sh:34 --
-- 40-postgrest-tubedepth-grants.sh:12-16), and since both of those only ever run on a db-store's first,
-- empty boot, cutting it off here is not something a re-run would undo.
