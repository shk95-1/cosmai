import csv, re, collections, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import wish_extractor as W
csv.field_size_limit(10**9)
S=W.S
vid={r['video_id']:r for r in csv.DictReader(open(S+'/videos.csv'))}
def norm(a): return re.sub(r'[^0-9a-zA-Z가-힣]','',(a or '').lower())
rows=list(csv.DictReader(open(S+'/comments.csv')))
creator_replied=set()
for r in rows:
    if not r['parent_id']: continue
    v=vid.get(r['video_id'],{})
    if (r['author_id'] and r['author_id']==v.get('channel_id')) or (v.get('channel') and norm(r['author'])==norm(v['channel'])):
        creator_replied.add(r['parent_id'])
cls={r['comment_id']:r['cls'] for r in csv.DictReader(open('wish_candidates.csv'))}
stat=collections.defaultdict(lambda:[0,0,0])  # n, hearted, creator_reply
for r in rows:
    if r['parent_id']: continue  # top-level only
    v=vid.get(r['video_id'],{})
    if (r['author_id'] and r['author_id']==v.get('channel_id')) or (v.get('channel') and norm(r['author'])==norm(v['channel'])): continue
    c=cls.get(r['comment_id'],'n')
    s=stat[c]; s[0]+=1; s[1]+= r['is_hearted_by_uploader']=='t'; s[2]+= r['comment_id'] in creator_replied
print('top-level non-creator comments; class | n | hearted% | creator-reply%')
for c in 'abcn':
    n,h,rp=stat[c]; print(c, n, f'{100*h/n:.1f}%', f'{100*rp/n:.1f}%')
# class a with entity vs without
ent={r['comment_id']:bool(r['brand'] or r['format'] or r['attribute']) for r in csv.DictReader(open('wish_candidates.csv')) if r['cls']=='a'}
st2=collections.defaultdict(lambda:[0,0,0])
for r in rows:
    if r['parent_id'] or r['comment_id'] not in ent: continue
    s=st2[ent[r['comment_id']]]; s[0]+=1; s[1]+= r['is_hearted_by_uploader']=='t'; s[2]+= r['comment_id'] in creator_replied
for k,(n,h,rp) in st2.items(): print('a entity=',k,n,f'{100*h/n:.1f}%',f'{100*rp/n:.1f}%')
# channels that react most to class a
ch=collections.defaultdict(lambda:[0,0,0])
for r in rows:
    if r['parent_id'] or cls.get(r['comment_id'])!='a': continue
    c=vid.get(r['video_id'],{}).get('channel','?'); s=ch[c]; s[0]+=1; s[1]+= r['is_hearted_by_uploader']=='t'; s[2]+= r['comment_id'] in creator_replied
print('channel | a_n | hearted | replied (top by a_n)')
for c,(n,h,rp) in sorted(ch.items(), key=lambda x:-x[1][0])[:12]: print(c,n,h,rp)
