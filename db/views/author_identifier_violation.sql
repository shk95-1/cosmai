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
-- (4) The collector's own table. Upstream #288 hashed the finite pre-rule population once, so the
-- invariant now covers history as well as new collection; a date cutoff would hide a regression in the
-- repaired rows.
-- The predicate asks what the value is **not**, not what it is: a `UC...`-shaped test would pass a handle
-- (`@name`), a legacy `/user/` id or anything else raw that is not that one shape, and every one of those
-- is as much an identifier as the shape we happen to recognise. Branch (5) already asks the display name
-- the open question; this is the same question asked of the id.
SELECT 'comment_raw_author_id'::text,
       'tubedepth.comments'::text,
       format('%s/%s', c.video_id, c.comment_id),
       format('first_seen_at=%s column=author_id', c.first_seen_at)
  FROM tubedepth.comments c
 WHERE c.author_id IS NOT NULL
   AND c.author_id !~ '^[0-9a-f]{24}$'
UNION ALL
-- (5) The display name is not stored at all, so its presence is the violation whatever it holds -- a
-- name-derived signal belongs in a feature column, never in the name itself (contracts/formats.md).
SELECT 'comment_author_name'::text,
       'tubedepth.comments'::text,
       format('%s/%s', c.video_id, c.comment_id),
       format('first_seen_at=%s column=author', c.first_seen_at)
  FROM tubedepth.comments c
 WHERE c.author IS NOT NULL
UNION ALL
-- (6) The allow-list, mirrored (fork #95). Branch (1) names two keys, so `author_name`,
-- `author_channel_id` or a whole `snippet` object carrying `authorDisplayName` passed it and the
-- loader alike. The seven keys below are the archive's own comment metadata, measured on all 247,338
-- of its comment rows, and they are the same tuple db/corpus/contract.py refuses against
-- (COMMENT_METADATA_KEYS) -- a test holds the two spellings together. Every one of them holds a
-- scalar, so an object can only ever arrive as a key of its own and this list is the whole question.
-- Key names, never values: a view the analysis role reads must not become the channel out for the
-- identifier it is complaining about.
SELECT 'corpus_comment_metadata_key'::text,
       'needs.corpus_document'::text,
       d.doc_id,
       format('snapshot=%s keys=%s', d.snapshot_id,
              (SELECT string_agg(k, ',' ORDER BY k)
                 FROM (SELECT jsonb_object_keys(d.source_metadata)
                       EXCEPT
                       SELECT unnest(ARRAY['author_channel_hash', 'collected_at', 'is_reply',
                                           'like_count', 'parent_comment_id', 'thread_id',
                                           'total_reply_count'])) u(k)))
  FROM needs.corpus_document d
 WHERE d.source = 'youtube_comment'
   AND EXISTS (SELECT jsonb_object_keys(d.source_metadata)
               EXCEPT
               SELECT unnest(ARRAY['author_channel_hash', 'collected_at', 'is_reply', 'like_count',
                                   'parent_comment_id', 'thread_id', 'total_reply_count']));

GRANT SELECT ON needs.author_identifier_violation TO needs_runtime;
