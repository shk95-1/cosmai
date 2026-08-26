-- 024: 트렌드 유형 판정과 기회 점수 (포크 이슈 #40). 추가만 (tests/test_ddl_additive_only.py).
--
-- ydc judge.py 는 지표 CSV 를 읽어 판정 컬럼을 붙인다. 지표 계산과 판정을 갈라 둔 이유는 판정 기준
-- (tau·가중치·유형 이름)이 팀 합의로 바뀌고 그때 지표를 다시 셀 필요가 없기 때문이며, 이 표는 그
-- 분리를 저장에서도 지킨다.
--
-- 이 표는 집계가 아니라 파생이다: 문서를 세지 않고 needs.metrics_topic_quarter 의 한 행을 받아 한 행을
-- 낸다. 그래서 이름이 metrics_ 로 시작하지 않고, contracts/formats.md §시간 의 "집계 그레인의 정본"
-- 표에 줄을 갖지 않는다 -- 언급 수·채널 수·지속성 중 어느 것도 들지 않으므로 정본을 다툴 상대가 없다.
CREATE TABLE needs.topic_quarter_judgement (
  -- 여덟 칸은 metrics_topic_quarter 의 기본키 그대로다. 판정 행이 자기 근거가 되는 지표 행 없이 설 수
  -- 없다는 것이 FK 이고, 그 FK 가 곧 "파생"의 기계적 형태다.
  run_id        bigint NOT NULL,
  scope         text NOT NULL,
  topic_key     text NOT NULL,
  quarter       text NOT NULL,
  source        text NOT NULL,
  content_type  text NOT NULL,
  panel_version int  NOT NULL,
  panel_role    text NOT NULL,
  -- 어휘가 아홉인 것도 뜻이다: 일곱이 유형이고 둘(판정 보류·미확정(진행 중))은 판정하지 않았다는 말이다.
  -- 오타 하나가 조용히 열 번째 유형을 열면 이 표를 GROUP BY 하는 자리가 유형 분포를 틀리게 읽는다.
  trend_type    text NOT NULL CHECK (trend_type IN (
                  '급상승','사라짐','지속 인기','단기 피크','신규 등장','채널 확산','근거 부족',
                  '판정 보류','미확정(진행 중)')),
  -- 이름만 있고 정의가 없으면 행이 자기 이름과 다른 것을 말한다 (022 의 sample_ok CHECK 과 같은 자리).
  judged        boolean NOT NULL
                CHECK (judged = (trend_type NOT IN ('근거 부족','판정 보류','미확정(진행 중)'))),
  -- 자리수가 곧 게이트의 해상도다: EVIDENCE_FLOOR 비교도 opportunity_score 의 항도 반올림된
  -- evidence_strength 를 쓴다 (contracts/interfaces.md §판정 "판정 자리수").
  evidence_strength numeric(4,1) NOT NULL CHECK (evidence_strength BETWEEN 0 AND 100),
  -- 점수를 매기지 않은 셀은 0 이 아니라 NULL 이다 -- 0 은 "가장 낮은 기회"라는 다른 말이다.
  opportunity_score numeric(4,1) CHECK (opportunity_score BETWEEN 0 AND 100),
  -- 근거 부족·판정 보류·미확정 셀은 점수 집합 밖이라 값이 있을 수 없다. 있으면 다른 집합에서 정규화된
  -- 점수이고, 그것은 이 표 안에서 조용히 다른 눈금이 된다.
  CHECK (opportunity_score IS NULL OR judged),
  gap_pp        numeric(6,2),              -- 100 * (댓글 composition - 영상 composition), (주제,분기) 사실
  -- 보류를 빈칸으로 두면 규칙의 구멍이 안 보인다. 사유는 닫힌 어휘이고 보류가 아닌 행은 '' 다.
  hold_reason   text NOT NULL DEFAULT '' CHECK (hold_reason IN (
                  '','no_prior_year','above_half_peak','within_tau_short_persistence','no_rule')),
  CHECK ((hold_reason <> '') = (trend_type = '판정 보류')),
  -- v1 은 언제나 true 다. 값이 늘 같은 칸을 두는 것은 TEAM_DECISIONS §3.2 의 `source_count < 2` 게이트가
  -- 꺼져 있다는 사실이 행에서 읽혀야 하기 때문이다 -- 플랫폼이 붙어 false 가 되는 날 그 조건이 켜진다.
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
