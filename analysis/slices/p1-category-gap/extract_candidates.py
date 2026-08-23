"""P1 1단계: 리뷰 → 문장 → 후보(표지 or 저평점) → aspect/polarity (aspects_generic + slice-suncare/polarity.py 재사용)."""
import csv, re, collections, sys
sys.path.insert(0, '/home/user1/github_prj/Main/architect/slice-p1-category-gap')
import aspects_generic as A
D = '/home/user1/github_prj/Main/architect/slice-p1-category-gap'
SPLIT = re.compile(r'(?<=[.!?~ㅠㅜ])\s+|(?<=요)\s+(?=[가-힣])|(?<=다)\s+(?=[가-힣])|(?<=[ㅎㅋ]{2})\s+')
def sentences(t):
    parts = [p.strip() for p in SPLIT.split(t or '') if p and len(p.strip()) > 4]
    return parts or ([t.strip()] if t and t.strip() else [])
def leaf(cat): return (cat or '').split(' > ')[-1]

# glowpick 카테고리 보강: rank_snapshot 없는 제품은 product.name 키워드로 추정
NAMES = {r['product_key']: r['name'] for r in csv.DictReader(open(f'{D}/glowpick_product_names.csv', encoding='utf-8'))}
NAME_CAT = [('선크림', r'선크림|썬크림|선스크린|선블록|선쿠션|선스틱|선세럼|선 ?젤'), ('쿠션', r'쿠션'), ('파운데이션', r'파운데이션|파데'),
            ('립틴트/라커', r'틴트|립 ?글로스|립스틱|립밤'), ('시트마스크', r'마스크|팩$|팩 '), ('샴푸', r'샴푸'), ('헤어트리트먼트', r'트리트먼트|헤어 ?마스크|헤어팩'),
            ('페이셜클렌저', r'클렌징|클렌저|폼'), ('에센스/세럼', r'세럼|에센스|앰플'), ('크림', r'크림'), ('스킨/토너', r'토너|스킨'), ('패드', r'패드'), ('아이섀도우', r'섀도|쉐도'), ('블러셔', r'블러셔|블러쉬')]
def gp_cat(r):
    if r['category_name']: return r['category_name'], 'rank_snapshot'
    n = NAMES.get(r['product_key'], '')
    for c, rx in NAME_CAT:
        if re.search(rx, n): return c, 'name_keyword'
    return '', 'none'
# glowpick/다이소 카테고리명 → 올영 leaf 사전 키로 매핑 (category-specific 사전 적용)
CAT_MAP = {'선크림': '선블록', '페이셜클렌저': '클렌징폼', '에센스/세럼': '에센스', '시트마스크': '시트팩', '파운데이션': '쿠션', 'BB/CC': 'BB/CC'}

seen = set(); out = []; skipped = collections.Counter()
for r in csv.DictReader(open(f'{D}/_reviews_raw.csv', encoding='utf-8')):
    key = (r['source'], r['review_key'])
    if key in seen: continue
    seen.add(key)
    cat_src = 'rank_snapshot'
    if r['source'] == 'glowpick': r['category_name'], cat_src = gp_cat(r)
    cat = leaf(r['category_name'])
    lex_cat = CAT_MAP.get(cat, cat)
    rating = float(r['rating']) if r['rating'] else None
    mk = A.complaint_marker_re(lex_cat)
    for s in sentences(r['body']):
        w = A.WISH.search(s); c = mk.search(s)
        kind = 'wish' if w else ('complaint' if c else None)
        if kind is None and not (rating is not None and rating <= 3): continue
        asp, pol, rule = A.classify(s, lex_cat)
        out.append(dict(site=r['source'], product_key=r['product_key'], review_key=r['review_key'], product_name=(r['product_name'] or NAMES.get(r['product_key'], ''))[:60],
                        category=cat, lexicon_category=lex_cat, category_src=cat_src, rating=rating, written_at=r['written_at'][:10],
                        kind=kind or 'low_rating', marker=(w or c).group(0) if (w or c) else f'rating={rating}', sentence=s,
                        aspect=asp or '', polarity=pol, rule=rule,
                        aspect_scope=('specific' if asp in {k.rstrip('~') for k in A.SPECIFIC.get(lex_cat, {})} else ('generic' if asp else ''))))
with open(f'{D}/candidates.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
print('reviews', len(seen), 'candidates', len(out))
print(collections.Counter((o['site'], o['polarity']) for o in out))
print('glowpick cat src', collections.Counter(o['category_src'] for o in out if o['site']=='glowpick'))
