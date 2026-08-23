import csv, collections
csv.field_size_limit(10**9)
men=list(csv.DictReader(open('brand_mentions.csv')))
cm=collections.defaultdict(collections.Counter); tr=collections.defaultdict(collections.Counter)
tot_cm=collections.Counter(); tot_tr=collections.Counter()
vids={r['video_id']:r for r in csv.DictReader(open('data/videos.csv'))}
for v,r in vids.items(): tot_tr[r['published_at'][:7]]+=1
for r in csv.DictReader(open('data/comments.csv')): tot_cm[r['published_at'][:7]]+=1
seen=set()
for r in men:
    m=r['published_at'][:7]
    if r['src']=='comment': cm[r['brand']][m]+=1
    elif (r['brand'],r['video_id']) not in seen:
        seen.add((r['brand'],r['video_id'])); tr[r['brand']][m]+=1
top=[b for b,_ in sorted(cm.items(), key=lambda x:-sum(x[1].values())) if b!='올리브영'][:20]
months=[f'{y}-{m:02d}' for y in (2025,2026) for m in range(1,13) if f'{y}-{m:02d}'<='2026-08']
with open('monthly_comment_mentions.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['brand']+months+['total'])
    w.writerow(['_all_comments']+[tot_cm[m] for m in months]+[sum(tot_cm[m] for m in months)])
    for b in top: w.writerow([b]+[cm[b][m] for m in months]+[sum(cm[b].values())])
with open('monthly_transcript_videos.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['brand']+months+['total'])
    w.writerow(['_all_videos']+[tot_tr[m] for m in months]+[sum(tot_tr[m] for m in months)])
    for b in top: w.writerow([b]+[tr[b][m] for m in months]+[sum(tr[b].values())])
print('comments per month:', [(m,tot_cm[m]) for m in months])
print('pre-2025 comments:', sum(v for k,v in tot_cm.items() if k<'2025'))
# 상승/하락: 2025H2(07-12) vs 2026(01-07) 점유율(해당 월 전체 댓글 대비 ‰)
print('\n| brand | 25-09..12 | 26-01..04 | 26-05..08 | ‰ P1 | ‰ P2 | ‰ P3 | trend(P3 vs P2) |')
print('|---|---|---|---|---|---|---|---|')
def S(b,ms): return sum(cm[b][m] for m in ms)
def T(ms): return sum(tot_cm[m] for m in ms)
H1=[m for m in months if '2025-09'<=m<='2025-12']; H2=[m for m in months if '2026-01'<=m<='2026-04']; H3=[m for m in months if '2026-05'<=m<='2026-08']
for b in top:
    a,bb,c=S(b,H1),S(b,H2),S(b,H3); sa,sb,sc=[1000*x/T(ms) for x,ms in ((a,H1),(bb,H2),(c,H3))]
    tr_=('↑' if sc>sb*1.5 else '↓' if sc<sb/1.5 else '→')
    print(f'| {b} | {a} | {bb} | {c} | {sa:.1f} | {sb:.1f} | {sc:.1f} | {tr_} |')
