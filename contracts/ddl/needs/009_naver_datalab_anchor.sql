-- Issue #248: the anchor point kept per request, not per (category, group_key, month).
--
-- `needs.naver_datalab_point`'s PK is (category, group_key, month), and the anchor group
-- (`collectors/naver/scope.py:DATALAB_ANCHOR`) is stored like any other group under that key. A
-- category that needs more than one
-- request (more than `collectors/naver/scope.py:DATALAB_CATEGORY_GROUPS_PER_REQUEST` groups) sends
-- the anchor in every request, but every batch upserts onto the same PK -- so only the last
-- request's anchor row survives, and the earlier batches' rows have no anchor of their own
-- `request_key` (db/views/naver_datalab_rescaled.sql's own header already described this).
--
-- This table is additive and keyed by the request instead: `request_key` is the sha256 of the whole
-- request body (`collectors/naver/parsing.py:datalab_request_key`), so it belongs to exactly one
-- category by construction and needs no `category` column of its own. One row per request and
-- month, never overwritten by a different request's anchor.
--
-- The anchor row is still written into `needs.naver_datalab_point` exactly as before -- #90's
-- readers keep reading it there; this table is an addition, not a move.

CREATE TABLE needs.naver_datalab_anchor (
    request_key text        NOT NULL,
    month       text        NOT NULL,
    ratio       numeric,
    captured_at timestamptz NOT NULL,
    PRIMARY KEY (request_key, month)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.naver_datalab_anchor TO needs_runtime;
