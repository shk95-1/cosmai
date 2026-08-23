import csv, collections, os
OUT = os.path.dirname(os.path.abspath(__file__))
rows = [r for r in csv.DictReader(open(OUT+'/wish_candidates.csv'))]
A = [r for r in rows if r['cls']=='a']; B = [r for r in rows if r['cls']=='b']
for r in rows: r['like']=int(r['like_count']); r['month']=r['published_at'][:7]
def cov(X):
    n=len(X); return dict(n=n, brand=sum(1 for r in X if r['brand']), format=sum(1 for r in X if r['format']), attr=sum(1 for r in X if r['attribute']),
        any=sum(1 for r in X if r['brand'] or r['format'] or r['attribute']), none=sum(1 for r in X if not(r['brand'] or r['format'] or r['attribute'])))
print('coverage a', cov(A)); print('coverage b', cov(B))
mon = collections.Counter(r['month'] for r in A); print('a by month', sorted(mon.items()))
print('a like dist: >=1', sum(1 for r in A if r['like']>=1), '>=5', sum(1 for r in A if r['like']>=5), '>=20', sum(1 for r in A if r['like']>=20), 'sum', sum(r['like'] for r in A))
def agg(X, keyf, label):
    g = collections.defaultdict(list)
    for r in X:
        k = keyf(r)
        if k: g[k].append(r)
    out=[]
    for k, rs in g.items():
        months = sorted(set(r['month'] for r in rs if '2025-09' <= r['month'] <= '2026-08'))
        likes = sum(r['like'] for r in rs); srt = sorted(rs, key=lambda r: -r['like']); top = srt[0]; ex2 = srt[1] if len(srt)>1 else top
        shape = 'narrow-strong' if len(rs) <= 15 and likes/len(rs) >= 10 else ('broad' if len(rs) >= 30 and len(set(r['channel'] for r in rs)) >= 10 else ('persistent' if len(months) >= 9 else 'thin'))
        out.append(dict(kind=label, key=k, mentions=len(rs), likes=likes, like_per_mention=round(likes/len(rs),1), months_present=len(months),
            first=min(r['month'] for r in rs), last=max(r['month'] for r in rs), videos=len(set(r['video_id'] for r in rs)), channels=len(set(r['channel'] for r in rs)),
            max_like=top['like'], shape=shape, example=top['sentence'][:160], example2=ex2['sentence'][:160], example2_like=ex2['like']))
    return sorted(out, key=lambda d: (-d['mentions'], -d['likes']))
def fa(r):
    f = r['format'].split(';')[0] if r['format'] else ''
    a = r['attribute'].split(';')[0] if r['attribute'] else ''
    return (f'{f} × {a}' if f and a else (f or a)) or ''
res = agg(A, fa, 'format×attr') + agg(A, lambda r: r['format'].split(';')[0] if r['format'] else '', 'format') \
    + agg(A, lambda r: r['attribute'].split(';')[0] if r['attribute'] else '', 'attribute') + agg(A, lambda r: r['brand'], 'brand') \
    + agg(B, lambda r: r['format'].split(';')[0] if r['format'] else '', 'b:format') + agg(B, lambda r: r['brand'], 'b:brand')
with open(OUT+'/wish_aggregates.csv','w',newline='') as f:
    w=csv.DictWriter(f, fieldnames=res[0].keys()); w.writeheader(); w.writerows(res)
for kind in ['format×attr','format','attribute','brand','b:format','b:brand']:
    print('\n##', kind)
    for d in [d for d in res if d['kind']==kind][:30]:
        print(f"{d['key']} | n={d['mentions']} likes={d['likes']} lpm={d['like_per_mention']} months={d['months_present']} vids={d['videos']} ch={d['channels']} max={d['max_like']} | {d['example'][:90]}")
# monthly series for top format×attr
print('\n## monthly a by top keys')
top = [d['key'] for d in res if d['kind']=='format×attr'][:12]
ms = sorted(m for m in mon if m>='2025-01')
for k in top:
    c = collections.Counter(r['month'] for r in A if fa(r)==k)
    print(k, ' '.join(f"{m[2:]}:{c[m]}" for m in ms))
