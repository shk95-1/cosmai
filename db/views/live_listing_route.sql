-- Which route answered the live lineage's listings (fork #95, from #91 option D and
-- contracts/ddl/tubedepth/005). `corpus_snapshot.instrument.listing_route` is one of the four keys
-- 030 names, and this view is the only way the projection can read it.
--
-- Why a view and not a query in db/corpus/project.py: `tubedepth.artifacts` is granted to
-- **needs_owner** and not to needs_runtime (db/grants/needs_runtime_reader.sql -- needs_runtime reads
-- the three snapshot tables and no collector state table). db/migrate.sh stage (f) creates this view
-- with SET ROLE needs_owner and a view runs with its owner's privilege, so this is the same shape
-- needs.collector_health already uses to reach jobs and artifacts. The alternative was one more line
-- in the grants file, which is upstream's to change and would open a collector state table to the
-- analysis role for one scalar.
--
-- The four listing kinds are collectors/youtube/cli.py's LISTING_KINDS, spelled out rather than
-- matched by prefix: 'video.metadata' is fetched over a route of its own (scope.json's
-- VIDEO_METADATA_ROUTE) and it is not what this key means.
--
-- No row for a route that never answered, and no row at all before the first listing fetch -- the
-- projection writes an empty list then, which is the honest answer and not a default route name.
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns
-- stay the same, so DROP goes first.

DROP VIEW IF EXISTS needs.live_listing_route;
CREATE VIEW needs.live_listing_route AS
SELECT a.fetch_route                AS listing_route,
       count(*)                     AS artifacts,
       min(a.fetched_at)            AS first_fetched_at,
       max(a.fetched_at)            AS last_fetched_at
  FROM tubedepth.artifacts a
 WHERE a.kind IN ('channel.videos', 'search.videos', 'playlist.items', 'trending.videos')
 GROUP BY a.fetch_route;

GRANT SELECT ON needs.live_listing_route TO needs_runtime;
