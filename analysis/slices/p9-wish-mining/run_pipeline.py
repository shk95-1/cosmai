import sys, csv, re, collections, random, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wish_extractor as W
S = W.S; OUT = os.path.dirname(os.path.abspath(__file__))
csv.field_size_limit(10**9)
vid = {r['video_id']: r for r in csv.DictReader(open(S+'/videos.csv'))}
def norm(a): return re.sub(r'[^0-9a-zA-Z가-힣]', '', (a or '').lower())
brands = W.load_brands(); BR = W.brand_regex(brands)
rows = list(csv.DictReader(open(S+'/comments.csv')))
cand = []; n_creator = 0; cls_cnt = collections.Counter()
for r in rows:
    v = vid.get(r['video_id'], {})
    ch = v.get('channel', '')
    is_creator = (r['author_id'] and r['author_id'] == v.get('channel_id')) or (ch and norm(r['author']) == norm(ch))
    if is_creator:
        n_creator += 1; continue
    c, m, s = W.classify(r['text'])
    cls_cnt[c] += 1
    if c == 'n': continue
    brand, fmt, attr = W.entities(s, brands, BR) if c == 'a' else W.entities(s, brands, BR)
    cand.append(dict(comment_id=r['comment_id'], video_id=r['video_id'], parent_id=r['parent_id'], published_at=r['published_at'][:10],
        like_count=int(r['like_count'] or 0), hearted=r['is_hearted_by_uploader'], channel=ch, video_title=v.get('title','')[:80],
        cls=c, marker=m, brand=brand, format=fmt, attribute=attr, sentence=s[:300], text=r['text'][:500]))
print('comments', len(rows), 'creator-authored dropped', n_creator, dict(cls_cnt))
with open(OUT+'/wish_candidates.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=cand[0].keys()); w.writeheader(); w.writerows(cand)
with open(OUT+'/wish_mention.csv', 'w', newline='') as f:
    cols = ['comment_id','video_id','published_at','like_count','class','brand','format','attribute','text']
    w = csv.writer(f); w.writerow(cols)
    for r in cand:
        if r['cls'] in 'ab': w.writerow([r['comment_id'], r['video_id'], r['published_at'], r['like_count'], r['cls'], r['brand'], r['format'], r['attribute'], r['sentence']])
