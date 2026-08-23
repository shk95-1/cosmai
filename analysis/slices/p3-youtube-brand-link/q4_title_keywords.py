import csv, re, collections, statistics as st
csv.field_size_limit(10**9)
vids=[r for r in csv.DictReader(open('data/videos.csv')) if r['view_count']]
for r in vids: r['views']=int(r['view_count'])
# 채널 중앙값 (채널당 영상 ≥3)
byc=collections.defaultdict(list)
for r in vids: byc[r['channel_id']].append(r['views'])
cmed={c:st.median(v) for c,v in byc.items() if len(v)>=3}
KW={'PDRN':r'PDRN|피디알엔','레티놀':r'레티놀|레티날','히알루론산':r'히알루론|히알','콜라겐':r'콜라겐','나이아신아마이드':r'나이아신','비타민C':r'비타민\s?C|비타민씨','세라마이드':r'세라마이드','펩타이드':r'펩타이드','시카':r'시카|병풀','글루타치온':r'글루타치온','엑소좀':r'엑소좀','AHA/BHA':r'AHA|BHA|PHA|각질',
 '선크림':r'선크림|선블록|자외선|선스틱|선쿠션|선세럼','쿠션':r'쿠션','파운데이션':r'파운데이션|파데','클렌징':r'클렌징|클렌저|세안','토너':r'토너|스킨','세럼/앰플':r'세럼|앰플|에센스','크림':r'크림','마스크팩':r'마스크팩|시트팩|팩','립':r'립|틴트','아이섀도우':r'섀도우|쉐도우|팔레트','블러셔':r'블러셔|블러쉬','샴푸':r'샴푸|두피','향수':r'향수|퍼퓸','미백':r'미백|잡티|기미','모공':r'모공','트러블/여드름':r'트러블|여드름|뾰루지','주름/탄력':r'주름|탄력|안티에이징|노화','수분':r'수분|보습|속건조','진정':r'진정|민감','톤업':r'톤업','파데프리':r'파데프리',
 '올리브영':r'올리브영|올영','다이소':r'다이소','세일':r'세일|할인|특가','신상':r'신상|신제품','추천':r'추천','리뷰/후기':r'리뷰|후기|솔직','내돈내산':r'내돈내산|광고❌|광고 ❌|노광고','하울':r'하울|털어','피부과/의사':r'피부과|의사|약사|전문가','남자':r'남자|맨즈|남성','쇼츠#':r'#shorts|#쇼츠','GRWM/메이크업':r'메이크업|화장법|GRWM','비교/vs':r'비교|VS|순위|TOP|1위|베스트'}
print(f'videos with views: {len(vids)}, with channel median (>=3 videos): {sum(1 for r in vids if r["channel_id"] in cmed)}')
allmed=st.median([r['views'] for r in vids])
rows=[]
for k,p in KW.items():
    rx=re.compile(p,re.I)
    hit=[r for r in vids if rx.search(r['title'])]
    if len(hit)<10: continue
    miss=[r for r in vids if not rx.search(r['title'])]
    ratio=[r['views']/cmed[r['channel_id']] for r in hit if r['channel_id'] in cmed and cmed[r['channel_id']]>0]
    ratio_miss=[r['views']/cmed[r['channel_id']] for r in miss if r['channel_id'] in cmed and cmed[r['channel_id']]>0]
    rows.append(dict(keyword=k,n=len(hit),median_views=int(st.median([r['views'] for r in hit])),median_views_rest=int(st.median([r['views'] for r in miss])),
        n_with_channel_norm=len(ratio),median_ratio_to_channel=round(st.median(ratio),2) if ratio else '',median_ratio_rest=round(st.median(ratio_miss),2) if ratio_miss else '',
        share_above_channel_median=round(sum(1 for x in ratio if x>1)/len(ratio),2) if ratio else ''))
rows.sort(key=lambda r:-(r['median_ratio_to_channel'] or 0))
with open('title_keyword_views.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print(f'overall median views {allmed:.0f}')
print('| keyword | n | median views | median views (others) | median views/channel-median | rest | share > channel median |')
print('|---|---|---|---|---|---|---|')
for r in rows: print(f"| {r['keyword']} | {r['n']} | {r['median_views']:,} | {r['median_views_rest']:,} | {r['median_ratio_to_channel']} | {r['median_ratio_rest']} | {r['share_above_channel_median']} |")
