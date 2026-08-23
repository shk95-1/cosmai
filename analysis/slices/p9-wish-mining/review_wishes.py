import sys, csv, os, random, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wish_extractor as W
csv.field_size_limit(10**9)
rows = list(csv.DictReader(open(W.S+'/reviews.csv')))
brands = W.load_brands(); BR = W.brand_regex(brands)
out = []; cnt = collections.Counter()
for r in rows:
    c, m, s = W.classify(r['body'] or '')
    cnt[c] += 1
    if c in 'ab':
        b, f, a = W.entities(s, brands, BR)
        out.append(dict(source=r['source'], review_key=r['review_key'], product_key=r['product_key'], rating=r['rating'], written_at=r['written_at'][:10], cls=c, marker=m, brand=b, format=f, attribute=a, sentence=s[:250]))
print('reviews', len(rows), dict(cnt))
print('by source', collections.Counter((r['source'], r['cls']) for r in out))
with open(os.path.dirname(os.path.abspath(__file__))+'/review_wish_candidates.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=out[0].keys()); w.writeheader(); w.writerows(out)
random.seed(5)
A = [r for r in out if r['cls']=='a']
for i, r in enumerate(random.sample(A, min(30, len(A))), 1): print(i, r['source'], r['rating'], r['marker'], '|', r['sentence'][:140])
