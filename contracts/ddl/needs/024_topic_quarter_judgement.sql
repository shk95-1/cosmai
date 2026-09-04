-- 024: the trend-type verdict and the opportunity score (fork issue #40). Additive only
-- (tests/test_ddl_additive_only.py).
--
-- ydc judge.py reads a metrics CSV and appends the verdict columns. Metric computation and verdict
-- are kept apart because the verdict's criteria (tau, weights, type names) change by team agreement
-- and the metrics need not be recounted then; this table keeps that separation in storage too.
--
-- This table is a derivation rather than an aggregate: it counts no document, takes one row of
-- needs.metrics_topic_quarter and emits one row. So its name does not start with metrics_, and it has
-- no line in the "canonical table per aggregate grain" table of contracts/formats.md §Time -- it
-- carries none of mention count, channel count or persistence, so it has no rival to be canonical over.
CREATE TABLE needs.topic_quarter_judgement (
  -- The eight columns are metrics_topic_quarter's primary key as it stands. That a verdict row
  -- cannot stand without the metric row grounding it is the FK, and that FK is the mechanical form
  -- of "derived".
  run_id        bigint NOT NULL,
  scope         text NOT NULL,
  topic_key     text NOT NULL,
  quarter       text NOT NULL,
  source        text NOT NULL,
  content_type  text NOT NULL,
  panel_version int  NOT NULL,
  panel_role    text NOT NULL,
  -- 어휘가 아홉인 것도 뜻이다: 일곱이 유형이고 둘(판정 보류·미확정(진행 중))은 판정하지 않았다는 말이다.
  -- One typo silently opening a tenth type makes every GROUP BY over this table read the type
  -- distribution wrong.
  trend_type    text NOT NULL CHECK (trend_type IN (
                  '급상승','사라짐','지속 인기','단기 피크','신규 등장','채널 확산','근거 부족',
                  '판정 보류','미확정(진행 중)')),
  -- A name with no definition makes a row say something other than its own name (the same place as
  -- 022's sample_ok CHECK).
  judged        boolean NOT NULL
                CHECK (judged = (trend_type NOT IN ('근거 부족','판정 보류','미확정(진행 중)'))),
  -- The decimal places are the resolution of the gate: both the EVIDENCE_FLOOR comparison and the
  -- opportunity_score term use the rounded evidence_strength
  -- (contracts/interfaces.md §Verdict's "verdict decimal places").
  evidence_strength numeric(4,1) NOT NULL CHECK (evidence_strength BETWEEN 0 AND 100),
  -- An unscored cell is NULL, not 0 -- 0 says something else, "the lowest opportunity".
  opportunity_score numeric(4,1) CHECK (opportunity_score BETWEEN 0 AND 100),
  -- 근거 부족·판정 보류·미확정 셀은 점수 집합 밖이라 값이 있을 수 없다. 있으면 다른 집합에서 정규화된
  -- 점수이고, 그것은 이 표 안에서 조용히 다른 눈금이 된다.
  CHECK (opportunity_score IS NULL OR judged),
  gap_pp        numeric(6,2),              -- 100 * (comment composition - video composition), a (topic, quarter) fact
  -- Leaving a hold blank hides the hole in the rules. The reason is a closed vocabulary, and a row
  -- that is not held is ''.
  hold_reason   text NOT NULL DEFAULT '' CHECK (hold_reason IN (
                  '','no_prior_year','above_half_peak','within_tau_short_persistence','no_rule')),
  CHECK ((hold_reason <> '') = (trend_type = '판정 보류')),
  -- v1 is always true. A column whose value never changes is kept because the fact that
  -- TEAM_DECISIONS §3.2's `source_count < 2` gate is off has to be readable off the row -- the day a
  -- platform is added and it turns false, that condition switches on.
  single_source boolean NOT NULL,
  judged_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role),
  FOREIGN KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role)
    REFERENCES needs.metrics_topic_quarter
);
-- What a screen asks is "what is surging in this quarter", not "this topic of this run". The primary
-- key starts with run_id and cannot give that path (the same reason as 022's (topic_key, quarter) index).
CREATE INDEX ON needs.topic_quarter_judgement (trend_type, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.topic_quarter_judgement TO needs_runtime;
