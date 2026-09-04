-- 024: the trend-type verdict and the opportunity score (fork issue #40). Additive only
-- (tests/test_ddl_additive_only.py).
--
-- ydc judge.py reads a metrics CSV and appends the verdict columns. Metric computation and verdict
-- are kept apart because the verdict's criteria (tau, weights, type names) change by team agreement
-- and the metrics need not be recounted then; this table keeps that separation in storage too.
--
-- 이 표는 집계가 아니라 파생이다: 문서를 세지 않고 needs.metrics_topic_quarter 의 한 행을 받아 한 행을
-- 낸다. 그래서 이름이 metrics_ 로 시작하지 않고, contracts/formats.md §시간 의 "집계 그레인의 정본"
-- 표에 줄을 갖지 않는다 -- 언급 수·채널 수·지속성 중 어느 것도 들지 않으므로 정본을 다툴 상대가 없다.
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
  -- 자리수가 곧 게이트의 해상도다: EVIDENCE_FLOOR 비교도 opportunity_score 의 항도 반올림된
  -- evidence_strength 를 쓴다 (contracts/interfaces.md §판정 "판정 자리수").
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
-- 화면이 묻는 것은 "이 분기에 무엇이 급상승인가"이지 "이 run 의 이 주제"가 아니다. 기본키는 run_id 로
-- 시작해서 그 길을 못 준다 (022 의 (topic_key, quarter) 인덱스와 같은 이유).
CREATE INDEX ON needs.topic_quarter_judgement (trend_type, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.topic_quarter_judgement TO needs_runtime;
