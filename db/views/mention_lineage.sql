-- 지표 한 칸을 만든 언급들과 그 언급의 원문 발췌 (#144 경로 4 · 5a · 5b).
--
-- A19 는 뒤집지 않는다. `need_mention`·`wish_mention` 은 `run_id` 를 갖지 않고(versioning.md), 집계가
-- 모집단을 고르는 술어는 `extractor_version = ANY(...)` 하나다(analysis/aggregate/pipeline.py 의
-- load_needs). "이 run 이 센 언급" 과 "같은 판본의 다른 언급" 은 정의상 같은 집합이라, run 별 부분집합을
-- 만들어 낼 자리가 애초에 없다. 그래서 이 뷰는 run 을 모른다 -- 칸의 축을 필터 가능한 컬럼으로 내놓고,
-- 어느 판본을 고를지는 화면이 `analysis_run.versions->>'extractor'` 에서 읽어 건다.
--
-- 걸지 말아야 할 것: `versions.polarity`. 한 extractor_version 이 polarity 두 판본을 담는다 -- run 26 에
-- 같이 걸면 neg 15,452 -> 8,685 (44 퍼센트 감소) 다(#144 판단 절 실측). `versions.polarity` 는 그 run 의
-- polarity 단계가 쓴 판본이지 집계 모집단이 아니다.
--
-- 문장은 **발췌만** 나간다: 120자에서 자르고 전문 길이를 나란히 둔다 -- 근거로 쓰기엔 충분하고
-- 전문 재구성은 안 된다(사용자 결정 2026-08-27). 원문 컬럼은 이름조차 나가지 않는다.
--
-- 이유를 정확히 적는다. anon 이 리뷰 본문을 못 본다는 것이 이유가 **아니다** -- 운영에서 그 선은
-- 이미 없다: postgrest_anon 이 trend_radar_reader 의 멤버라 `trend_radar.review.body` 를 그대로
-- 읽는다(코디네이터 실측 2026-08-27). db/grants/postgrest_anon_needs.sql 은 needs 스키마만 다스린다.
-- 이 뷰가 자르는 이유는 **이 뷰가 원문 전달 경로가 되지 않게** 하려는 것이다: 지표 한 칸에서
-- 언급 수천 건이 딸려 나오는 자리라, 발췌가 아니면 여기가 사실상의 원문 덤프 출구가 된다.
-- 그 별건 노출(anon 이 trend_radar 를 읽는 것 자체)은 이 이슈의 몫이 아니다.
--
-- 필터 없이 부르면 need_mention 전부(운영 183,571행)를 훑는다. 화면은 언제나 칸의 축으로 좁혀
-- 부르고(PGRST_DB_MAX_ROWS=1000, mention_id 정렬로 이어 읽는다), 이 뷰는 그 쓰임에 맞춰져 있다.
--
-- LIKE 를 쓰지 않는다: 그 와일드카드 문자를 이 파일을 드라이버로 실행하는 쪽(psycopg)이 플레이스홀더로
-- 읽어 죽는다 -- 주석에 한 글자만 있어도 그렇다(db/views/pipeline_health.sql 이 데인 자리).
--
-- db/migrate.sh (f) 가 배포마다 DROP + CREATE 한다.

DROP VIEW IF EXISTS needs.mention_lineage;
CREATE VIEW needs.mention_lineage AS
WITH mention AS (
    SELECT
        'need'::text                            AS kind,
        m.mention_id,
        m.extractor_version,
        m.src,
        m.site,
        m.ref,
        NULL::text                              AS parent_hint,
        coalesce(m.category, '')                AS category,
        m.need_key,
        -- A17: scope='all' 롤업만 needs.need_key.canonical 로 동의어를 접는다. 두 값을 나란히 두어야
        -- 카테고리 칸(raw)과 롤업 칸(canonical)이 같은 뷰에서 갈린다.
        coalesce(k.canonical, m.need_key)       AS need_key_rollup,
        m.month,
        -- 제품 축의 값 (analysis/aggregate/__init__.py 의 _product): product_ref 가 없으면
        -- source_product_key, 그것도 없으면 '' -- 그 '' 가 카테고리 합 행이다.
        coalesce(nullif(m.product_ref, ''), nullif(m.source_product_key, ''), '') AS product_axis,
        NULL::text                              AS wish_class,
        ''::text                                AS format_first,
        ''::text                                AS attribute_first,
        ''::text                                AS brand,
        m.polarity,
        NULL::int                               AS like_count,
        m.observed_at,
        m.observed_at_resolution,
        m.sentence
    FROM needs.need_mention m
    LEFT JOIN needs.need_key k ON k.need_key = m.need_key
    UNION ALL
    SELECT
        'wish',
        w.mention_id,
        w.extractor_version,
        w.src,
        -- wish_mention 에는 site 컬럼이 없다. 댓글은 유튜브 하나뿐이라 여기서 정해지지만, 리뷰
        -- 갈래는 어느 사이트인지 말할 값이 없어 NULL 이고 그래서 원문에도 닿지 못한다(아래 doc_kind).
        CASE WHEN w.src = 'yt_comment' THEN 'youtube' END,
        w.ref,
        w.video_id,
        '',
        NULL,
        NULL,
        w.month,
        coalesce(w.product_ref, ''),
        w.wish_class,
        -- format 은 ';' 로 최대 3개가 들어오고 첫 번째가 주 값이다 (A12, aggregate/__init__.py 의 _first).
        coalesce(split_part(w.format, ';', 1), ''),
        coalesce(split_part(w.attribute, ';', 1), ''),
        coalesce(w.brand, ''),
        -- wish 는 불만/만족 축이 없다. 없는 값을 '중립' 같은 것으로 채우지 않는다.
        NULL,
        w.like_count,
        w.observed_at,
        w.observed_at_resolution,
        w.sentence
    FROM needs.wish_mention w
),
located AS (
    SELECT
        m.*,
        -- ref 는 리뷰가 product_key/review_key, 댓글이 video_id/comment_id 다 (001_needs.sql 의 주석).
        -- 원문 표가 없는 갈래(yt_transcript·naver_blog)와 사이트를 모르는 wish 리뷰는 여기서 NULL 이
        -- 되고, 아래 두 조인이 아예 걸리지 않는다 -- 행은 남고 doc_found 만 거짓이다.
        CASE WHEN m.src = 'review' AND m.site IS NOT NULL THEN 'review'
             WHEN m.src = 'yt_comment' THEN 'yt_comment' END               AS doc_kind,
        CASE WHEN strpos(m.ref, '/') > 0 THEN split_part(m.ref, '/', 1)
             ELSE m.parent_hint END                                        AS doc_parent,
        CASE WHEN strpos(m.ref, '/') > 0 THEN split_part(m.ref, '/', 2)
             ELSE m.ref END                                                AS doc_key
    FROM mention m
)
SELECT
    l.kind,
    l.mention_id,
    l.extractor_version,
    l.src,
    l.site,
    l.ref,
    l.category,
    l.need_key,
    l.need_key_rollup,
    l.month,
    l.product_axis,
    l.wish_class,
    l.format_first,
    l.attribute_first,
    l.brand,
    l.polarity,
    l.like_count,
    l.observed_at,
    l.observed_at_resolution,
    left(l.sentence, 120)                       AS sentence_excerpt,
    -- 잘렸다는 사실은 숨기지 않는다 -- 전문 길이가 나란히 있어야 발췌인 줄 안다.
    length(l.sentence)                          AS sentence_chars,
    l.doc_kind,
    l.doc_parent,
    l.doc_key,
    (r.review_key IS NOT NULL OR c.comment_id IS NOT NULL) AS doc_found,
    left(coalesce(r.body, c.text), 120)         AS doc_excerpt,
    length(coalesce(r.body, c.text))            AS doc_chars,
    coalesce(r.written_at, c.published_at)      AS doc_at,
    r.rating                                    AS doc_rating,
    c.like_count                                AS doc_like_count
FROM located l
-- review 의 PK 는 (source, review_key) 다. site 를 같이 걸지 않으면 다른 사이트의 같은 review_key 가
-- 붙어 한 언급이 여러 행이 된다.
LEFT JOIN trend_radar.review r
       ON l.doc_kind = 'review' AND r.source = l.site AND r.review_key = l.doc_key
LEFT JOIN tubedepth.comments c
       ON l.doc_kind = 'yt_comment' AND c.video_id = l.doc_parent AND c.comment_id = l.doc_key;

GRANT SELECT ON needs.mention_lineage TO needs_runtime;
-- 화면은 PostgREST 에 anon 으로 묻는다. 이 GRANT 가 db/grants/postgrest_anon_needs.sql 에 있으면
-- 살아남지 못한다: 그 파일은 migrate 단계 (d) 이고 뷰를 DROP 하고 다시 만드는 것은 (f) 라, 새 객체에
-- 옛 GRANT 가 따라오지 않는다. 뷰의 권한은 뷰가 소유한다(#158 -- 화면이 401 이었다).
GRANT SELECT ON needs.mention_lineage TO postgrest_anon;
-- 권한이 바뀌었으니 PostgREST 의 스키마 캐시를 깨운다. (d) 의 NOTIFY 는 이 뷰가 만들어지기 전에 돈다.
NOTIFY pgrst, 'reload schema';
