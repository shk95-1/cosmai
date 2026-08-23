-- 분석이 원천 스키마를 읽기 위한 SELECT 전용 권한. DDL 도 DML 도 부여하지 않는다.
-- 근거: analysis/slices/**/{load_db.py,ctx.py,q*.py} 와 각 슬라이스의 _raw CSV 헤더 (감사 (D), T1·T2·T12).
-- 슈퍼유저(platform)가 실행한다 — needs_migrator 는 남의 스키마에 GRANT 할 수 없다.
-- 스키마가 없는 곳(테스트 컨테이너)에서도 돌아야 하므로 존재할 때만 실행한다.

SELECT format('GRANT USAGE ON SCHEMA %I TO needs_runtime', n)
FROM (VALUES ('trend_radar'), ('tubedepth')) v(n)
WHERE EXISTS (SELECT FROM pg_namespace WHERE nspname = n) \gexec

-- trend_radar: 제품·랭킹·가격·리뷰·사이트 구조화 축
-- tubedepth: 댓글·자막·영상 메타·목록
SELECT format('GRANT SELECT ON %s TO needs_runtime', t)
FROM (VALUES
    ('trend_radar.product'),         -- p2 load_db.py:5 (source,product_key,name,brand,volume,first_seen_at,ingredients)
    ('trend_radar.rank_snapshot'),   -- p2 load_db.py:4 · p3 rank_snapshot.csv · p9 브랜드·카테고리
    ('trend_radar.price_point'),     -- p2 load_db.py:4 · q4_price_rank.py:18 (captured_at,price,discount_rate)
    ('trend_radar.new_product'),     -- p2 load_db.py:6 · q6_new.py:4-6 (listed_at)
    ('trend_radar.review'),          -- p1·suncare·p9 (rating,written_at,body) ← 컬럼명은 body 다 (T3)
    ('trend_radar.review_stats'),    -- p1 review_stats.csv (review_count,pct_1..pct_5) = 모집단 분모
    ('trend_radar.review_topic'),    -- p1 site_topic_raw.csv (topic_group,topic_name,share_pct)
    ('trend_radar.review_answer'),   -- p1 site_answer_raw.csv (question_name,answer) = 다이소 설문 축
    ('tubedepth.comments'),          -- p9 export_data.sh:5 · p3 (video_id,comment_id,like_count,text)
    ('tubedepth.transcripts'),       -- p3·suncare (video_id,language,full_text,segment_count)
    ('tubedepth.video_snapshots'),   -- p9 export_data.sh:6 · p3 (title,channel_id,view_count) ← #2 의 "videos" (T1)
    ('tubedepth.listing_entries')    -- p3 q4_trending.py:13 (kind,target,video_id,title,channel_id)
) v(t)
WHERE to_regclass(t) IS NOT NULL \gexec

-- 미래 테이블까지 자동으로 열리게 하지 않는다: DEFAULT PRIVILEGES 는 일부러 부여하지 않는다.
-- 새 원천 테이블을 읽어야 하면 여기 한 줄을 더하고 근거(슬라이스 file:line)를 주석에 남긴다.
-- 읽지 않는 것(근거): trend_radar.review_summary · run/run_source/fetch_log(5단계 운영 뷰)
-- · tubedepth.channel_snapshots · tubedepth 수집기 내부 상태 테이블 · cosmai.* 전부.
