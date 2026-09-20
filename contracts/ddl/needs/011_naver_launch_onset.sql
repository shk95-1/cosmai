-- 011: the DataLab launch-onset axis (#285) -- the monthly series it reads, and the one widened
-- vocabulary it needs. Upstream block 006~019 (contracts/versioning.md); 001..010 are already in
-- needs.schema_migration, so this number is the next free one and no earlier file is edited.
--
-- The axis: for a product with a line-specific term list, the month its own term first reaches 10%
-- of that term's own peak is an upper bound on the launch -- `not_after`, month precision, written
-- into needs.product_launch_evidence under axis 'datalab_onset' (#282 §Launch evidence). The claim
-- is the ledger's; what this file adds is the evidence under it.
--
-- Why the series does not go into needs.naver_datalab_point, which already holds monthly DataLab
-- rows:
--   * that table's subject is a (lexicon category, keyword group) pair and this one's is a
--     `product_ref`, so a product would have to be smuggled in as a group name;
--   * every row of it is read by db/views/naver_datalab_rescaled.sql against the anchor group of
--     its own request_key (#90, #248). A launch-onset request carries **one** keyword group on
--     purpose -- a relative ratio is per request, so a small product beside a large one rounds to
--     zero, while a term's onset is relative to its own peak and needs no anchor at all -- so every
--     one of these rows would rescale to NULL and dilute the one signal #250 left behind;
--   * the shopping-insight series has no home there at all: it is a second API, keyed by the single
--     term of its keyword group rather than by a group of terms.

CREATE TABLE needs.naver_launch_series (
  -- The canonical product the terms were derived for. The FK is the same one the evidence ledger
  -- takes (010): a series naming a product the catalogue does not hold could never become a claim.
  product_ref  text        NOT NULL REFERENCES needs.product_ref,
  -- Which of the two APIs answered. Closed, because the later-of-two rule in
  -- collectors/naver/onset.py reads it: a third API is a decision about what the claim means, not
  -- an insert. search_trend = POST /search-trend/v1/search (monthly from 2016-01);
  -- shopping_insight = POST /shopping/v1/category/keywords (monthly from 2017-08).
  api          text        NOT NULL CHECK (api IN ('search_trend', 'shopping_insight')),
  -- Which series inside that API's answer. search_trend sends one keyword group holding every term
  -- of the product, so it has exactly one series and this is ''. shopping_insight takes one term
  -- per keyword group (the vendor's own shape, measured 2026-09-20), so it has one series per term
  -- and this is that term. Empty is therefore meaningful rather than missing, and the two APIs
  -- never collide in the key because `api` is in it.
  series_key   text        NOT NULL,
  month        text        NOT NULL,              -- 'YYYY-MM', the same grain naver_datalab_point keeps
  -- The vendor's own relative ratio, stored raw like every other DataLab row (#44). It is relative
  -- to this series' own maximum inside this request, which is exactly what the onset rule wants and
  -- the reason no anchor rides along.
  ratio        numeric,
  -- What was actually searched, for the audit the term list owes: the whole term set for
  -- search_trend, the one term for a shopping_insight series.
  terms        jsonb       NOT NULL,
  -- The request that produced it (collectors/naver/parsing.py:datalab_request_key), so a row can be
  -- traced to the exact body that was sent. It is NOT the claim's source_ref: endDate moves every
  -- month, so a request key would mint a new claim per run instead of re-stating one.
  request_key  text        NOT NULL,
  captured_at  timestamptz NOT NULL,
  PRIMARY KEY (product_ref, api, series_key, month)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON needs.naver_launch_series TO needs_runtime;

-- The one widening. needs.naver_run.dataset (004) closes the collector's dataset vocabulary with a
-- CHECK, and `launch_onset` is a third dataset of the same collector -- one run row, one fetch
-- journal, one line in needs.collector_health and needs.pipeline_health, exactly like datalab and
-- blog. Widening a closed vocabulary is what pre-approval 2 prices at a human-approved DROP
-- CONSTRAINT (the same sentence 010 and 028 write about their own CHECKs), which is why this is
-- registered in tests/test_ddl_additive_only.py's SANCTIONED_DESTRUCTIVE rather than slipped past
-- it. Nothing is dropped but the constraint itself and no row changes: every existing value stays
-- legal under the replacement. **User approval 2026-09-20.**
--
-- When not to apply it: not on the 1st of a month between 06:10 and 07:20 UTC, which is when
-- stack/crontab.d/collector-naver runs the three naver passes and each of them holds a
-- needs.naver_run row open. The ALTER takes ACCESS EXCLUSIVE on that table, and db/migrate.sh
-- step (c) sets lock_timeout = 5s, so a collision rolls the migration back cleanly for a retry
-- rather than blocking a collector -- but the retry is avoidable by not deploying in that hour.
-- (The crontab's own times are UTC; its header says so.)
ALTER TABLE needs.naver_run DROP CONSTRAINT naver_run_dataset_check;
ALTER TABLE needs.naver_run
  ADD CONSTRAINT naver_run_dataset_check CHECK (dataset IN ('datalab', 'blog', 'launch_onset'));
