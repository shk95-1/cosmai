-- Asks the author-identifier rule back against the stored rows. Empty means true (#91 decision 3, #92):
-- a comment row keeps the author's channel id only as sha256("youtube:" + channel_id)[:24] and keeps no
-- display name.
-- The rule spans two stores, so no CHECK on one table can hold it: `needs.corpus_document` is where the
-- handover corpus landed already hashed, and `tubedepth.comments` is where the live collector writes.
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns stay
-- the same, so DROP goes first.
-- No branch selects the offending value, only the column that holds it: a view the analysis role reads
-- must not become the channel out for the identifier it is complaining about.

DROP VIEW IF EXISTS needs.author_identifier_violation;
CREATE VIEW needs.author_identifier_violation AS
-- (1) The corpus keeps the author as a hash and nothing else, so a name-shaped key in the metadata is a
-- loader that was bypassed -- db/corpus refuses these before the insert (db/corpus/contract.py).
SELECT 'corpus_author_key'::text                                        AS violation,
       'needs.corpus_document'::text                                    AS relation,
       d.doc_id                                                         AS row_key,
       format('snapshot=%s keys=%s', d.snapshot_id,
              (SELECT string_agg(k, ',' ORDER BY k)
                 FROM jsonb_object_keys(d.source_metadata) k
                WHERE k IN ('author', 'author_id')))                    AS detail
  FROM needs.corpus_document d
 WHERE d.source_metadata ?| ARRAY['author', 'author_id']
UNION ALL
-- (2) A hash the length and alphabet of a channel id is not a hash. "UC" + 22 base64url characters is the
-- raw form, and the same pattern is db/corpus/author.py's RAW_CHANNEL_ID.
SELECT 'corpus_raw_channel_hash'::text,
       'needs.corpus_document'::text,
       d.doc_id,
       format('snapshot=%s key=author_channel_hash', d.snapshot_id)
  FROM needs.corpus_document d
 WHERE d.source_metadata ->> 'author_channel_hash' ~ '^UC[0-9A-Za-z_-]{22}$'
UNION ALL
-- (3) The other half of the same question: a value of the right kind and the wrong shape. An untruncated
-- or upper-case digest is not caught by (2) and matches no creator comment either, so the marking empties
-- with nothing to read it off (db/corpus/author.py's AUTHOR_HASH is the same pattern).
SELECT 'corpus_hash_shape'::text,
       'needs.corpus_document'::text,
       d.doc_id,
       format('snapshot=%s key=author_channel_hash', d.snapshot_id)
  FROM needs.corpus_document d
 WHERE d.source_metadata ? 'author_channel_hash'
   AND d.source_metadata ->> 'author_channel_hash' !~ '^[0-9a-f]{24}$'
   AND d.source_metadata ->> 'author_channel_hash' !~ '^UC[0-9A-Za-z_-]{22}$'
UNION ALL
-- (4) The collector's own table. Cutoff: the last pre-rule comment row was first seen 2026-08-23T22:55:44Z
-- and the collector has been stopped since; every row after this date is collected under the hash rule
-- (#92), so the view must see it. A later cutoff would leave the first days of live collection unwatched.
-- The predicate asks what the value is **not**, not what it is: a `UC...`-shaped test would pass a handle
-- (`@name`), a legacy `/user/` id or anything else raw that is not that one shape, and every one of those
-- is as much an identifier as the shape we happen to recognise. Branch (5) already asks the display name
-- the open question; this is the same question asked of the id.
SELECT 'comment_raw_author_id'::text,
       'tubedepth.comments'::text,
       format('%s/%s', c.video_id, c.comment_id),
       format('first_seen_at=%s column=author_id', c.first_seen_at)
  FROM tubedepth.comments c
 WHERE c.first_seen_at > '2026-08-24'
   AND c.author_id IS NOT NULL
   AND c.author_id !~ '^[0-9a-f]{24}$'
UNION ALL
-- (5) The display name is not stored at all, so its presence is the violation whatever it holds -- a
-- name-derived signal belongs in a feature column, never in the name itself (contracts/formats.md).
SELECT 'comment_author_name'::text,
       'tubedepth.comments'::text,
       format('%s/%s', c.video_id, c.comment_id),
       format('first_seen_at=%s column=author', c.first_seen_at)
  FROM tubedepth.comments c
 WHERE c.first_seen_at > '2026-08-24'
   AND c.author IS NOT NULL;

GRANT SELECT ON needs.author_identifier_violation TO needs_runtime;
