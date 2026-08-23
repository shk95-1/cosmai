import csv, collections
csv.field_size_limit(10**9)
lex=list(csv.DictReader(open('brand_lexicon.csv')))
canon={r['surface']:r['canonical'] for r in lex}
# 최신 스냅샷 per (source, product_key)
latest={}
for r in csv.DictReader(open('data/rank_snapshot.csv')):
    k=(r['source'],r['product_key'])
    if k not in latest or r['captured_at']>latest[k]['captured_at']: latest[k]=r
rank=collections.defaultdict(lambda: {'oy_best':None,'oy_n':0,'gp_best':None,'gp_n':0,'ds_n':0,'oy_boards':set()})
for (s,pk),r in latest.items():
    b=canon.get(r['brand'],r['brand'])
    if not b: continue
    rk=int(r['rank']) if r['rank'] else None
    d=rank[b]
    if s=='oliveyoung':
        d['oy_n']+=1; d['oy_boards'].add(r['board'])
        if rk and (d['oy_best'] is None or rk<d['oy_best']): d['oy_best']=rk
    elif s=='glowpick':
        d['gp_n']+=1
        if rk and (d['gp_best'] is None or rk<d['gp_best']): d['gp_best']=rk
    elif s=='daisomall': d['ds_n']+=1
vids={r['video_id']:r for r in csv.DictReader(open('data/videos.csv'))}
yt=collections.defaultdict(lambda: {'videos':set(),'title_videos':set(),'comment_docs':0,'comment_videos':set()})
for r in csv.DictReader(open('brand_mentions.csv')):
    b=r['brand']; d=yt[b]
    if r['src'] in('title','transcript'):
        d['videos'].add(r['video_id'])
        if r['src']=='title': d['title_videos'].add(r['video_id'])
    else:
        d['comment_docs']+=1; d['comment_videos'].add(r['video_id'])
rows=[]
for b in set(yt)|set(rank):
    y=yt.get(b,{'videos':set(),'title_videos':set(),'comment_docs':0,'comment_videos':set()}); rk=rank.get(b)
    views=sum(int(vids[v]['view_count'] or 0) for v in y['videos'] if v in vids)
    rows.append(dict(brand=b, n_videos=len(y['videos']), n_title_videos=len(y['title_videos']), comment_mentions=y['comment_docs'], n_comment_videos=len(y['comment_videos']),
        views_of_mentioning_videos=views, oy_best_rank=(rk['oy_best'] if rk and rk['oy_best'] is not None else ''), oy_n_products=rk['oy_n'] if rk else 0, oy_n_boards=len(rk['oy_boards']) if rk else 0,
        gp_best_rank=(rk['gp_best'] if rk and rk['gp_best'] is not None else ''), gp_n_products=rk['gp_n'] if rk else 0, ds_n_products=rk['ds_n'] if rk else 0,
        yt_score=len(y['videos'])+y['comment_docs']/10))
rows.sort(key=lambda r:-r['yt_score'])
with open('brand_mention_vs_rank.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print('| brand | videos(title+tr) | title | comments | views(M) | OY best | OY #prod | GP best | GP #prod | Daiso #prod |')
print('|---|---|---|---|---|---|---|---|---|---|')
for r in [x for x in rows if x['brand']!='올리브영'][:40]:
    print(f"| {r['brand']} | {r['n_videos']} | {r['n_title_videos']} | {r['comment_mentions']} | {r['views_of_mentioning_videos']/1e6:.1f} | {r['oy_best_rank']} | {r['oy_n_products']} | {r['gp_best_rank']} | {r['gp_n_products']} | {r['ds_n_products']} |")
# 상관: 스피어만 (yt_score vs oy_n_products) among brands with any
import math
def spearman(a,b):
    def rk(x):
        s=sorted(range(len(x)),key=lambda i:x[i]); r=[0]*len(x)
        i=0
        while i<len(s):
            j=i
            while j+1<len(s) and x[s[j+1]]==x[s[i]]: j+=1
            for k in range(i,j+1): r[s[k]]=(i+j)/2
            i=j+1
        return r
    ra,rb=rk(a),rk(b); n=len(a); ma=sum(ra)/n; mb=sum(rb)/n
    cov=sum((ra[i]-ma)*(rb[i]-mb) for i in range(n)); va=sum((x-ma)**2 for x in ra); vb=sum((x-mb)**2 for x in rb)
    return cov/math.sqrt(va*vb)
sub=[r for r in rows if r['oy_n_products']>0]
print('\nOY-ranked brands:',len(sub),' spearman(yt_score, oy_n_products)=',round(spearman([r['yt_score'] for r in sub],[r['oy_n_products'] for r in sub]),3))
sub2=[r for r in sub if r['n_videos']>0]
print('OY-ranked & mentioned:',len(sub2),' spearman(yt_score, oy_best_rank)=',round(spearman([r['yt_score'] for r in sub2],[r['oy_best_rank'] for r in sub2]),3))
print('OY-ranked brands with zero YouTube mention:',sum(1 for r in sub if r['n_videos']==0 and r['comment_mentions']==0))
print('\nLOUD but unranked (no OY/GP product), top 25 by yt_score:')
for r in [x for x in rows if x['oy_n_products']==0 and x['gp_n_products']==0 and x['brand']!='올리브영'][:25]:
    print(f"  {r['brand']}: videos {r['n_videos']}, comments {r['comment_mentions']}, views {r['views_of_mentioning_videos']/1e6:.1f}M, daiso {r['ds_n_products']}")
print('\nRANKED high but quiet: OY >=8 products or best<=3, videos<=3 and comments<=10:')
for r in sorted([x for x in rows if (x['oy_n_products']>=8 or (x['oy_best_rank']!='' and x['oy_best_rank']<=3)) and x['n_videos']<=3 and x['comment_mentions']<=10],key=lambda x:-x['oy_n_products'])[:25]:
    print(f"  {r['brand']}: OY best {r['oy_best_rank']} #prod {r['oy_n_products']} boards {r['oy_n_boards']}, videos {r['n_videos']}, comments {r['comment_mentions']}")
