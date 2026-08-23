"""P1 2단계: candidates → need_mention(리뷰 단위 중복 제거) → 카테고리×aspect 지표 + 모집단 점유율(review_stats 분모)."""
import csv, collections, sys
sys.path.insert(0, '/home/user1/github_prj/Main/architect/slice-p1-category-gap'); import aspects_generic as A
D = '/home/user1/github_prj/Main/architect/slice-p1-category-gap'
cands = list(csv.DictReader(open(f'{D}/candidates.csv', encoding='utf-8')))
stats = {r['product_key']: r for r in csv.DictReader(open(f'{D}/review_stats.csv', encoding='utf-8'))}
reviews = {}
for r in csv.DictReader(open(f'{D}/_reviews_raw.csv', encoding='utf-8')):
    reviews.setdefault((r['source'], r['review_key']), r)
# ---- need_mention: (site, product, review, aspect, polarity) 리뷰 단위 1건 (문장 여러 개 → 1)
nm = {}
for c in cands:
    if not c['aspect'] or c['polarity'] not in ('불만', '만족'): continue
    k = (c['site'], c['product_key'], c['review_key'], c['aspect'], c['polarity'])
    if k in nm: continue
    nm[k] = dict(site=c['site'], product_key=c['product_key'], review_key=c['review_key'], category=c['category'], lexicon_category=c['lexicon_category'],
                 need_key=c['aspect'], aspect_scope=c['aspect_scope'], polarity=c['polarity'], rating=c['rating'], month=c['written_at'][:7], sentence=c['sentence'][:200])
nm = list(nm.values())
with open(f'{D}/need_mention.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(nm[0].keys())); w.writeheader(); w.writerows(nm)
print('need_mention', len(nm))

# ---- 제품별 저평점 전수 여부 + 분모
oy = [r for r in reviews.values() if r['source'] == 'oliveyoung']
per_prod = collections.defaultdict(lambda: dict(n=0, low=0, has3=False))
for r in oy:
    p = per_prod[r['product_key']]; p['n'] += 1; rt = float(r['rating'] or 0)
    if rt <= 2: p['low'] += 1
    if rt == 3: p['has3'] = True
prod_rows = []
for pk, p in per_prod.items():
    s = stats.get(pk); rc = int(s['review_count']) if s else 0
    site_low = round(rc * (int(s['pct_1'] or 0) + int(s['pct_2'] or 0)) / 100) if s and s['pct_1'] else None
    complete = p['low'] < 150 or p['has3']   # RATING_ASC 150 안에 3점이 섞였거나 ≤2점이 150 미만이면 ≤2점은 전수
    cat = next((r['category_name'] for r in oy if r['product_key'] == pk), '').split(' > ')[-1]
    prod_rows.append(dict(product_key=pk, category=cat, collected=p['n'], low_collected=p['low'], low_complete=complete, site_review_count=rc, site_low_est=site_low))
with open(f'{D}/product_denominator.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(prod_rows[0].keys())); w.writeheader(); w.writerows(prod_rows)
denom = {r['product_key']: r for r in prod_rows}

# ---- 카테고리 × aspect 지표 (올리브영) + 저평점 전수 aspect 언급(극성 무관)
low_mention = collections.defaultdict(set)   # (cat, aspect) -> set(review_key) among ≤2★ complete products, any polarity with aspect
for c in cands:
    if c['site'] != 'oliveyoung' or not c['aspect'] or not c['rating'] or float(c['rating']) > 2: continue
    if not denom[c['product_key']]['low_complete']: continue
    low_mention[(c['category'], c['aspect'])].add((c['product_key'], c['review_key']))
cat_site_total = collections.defaultdict(int); cat_low_total = collections.defaultdict(int); cat_prods = collections.defaultdict(set)
for pk, d in denom.items():
    cat_prods[d['category']].add(pk)
    if d['low_complete']: cat_site_total[d['category']] += d['site_review_count']; cat_low_total[d['category']] += d['low_collected']
agg = collections.defaultdict(lambda: dict(neg=0, pos=0, prods_neg=set(), months=set()))
for m in nm:
    if m['site'] != 'oliveyoung': continue
    a = agg[(m['category'], m['need_key'])]
    a['neg' if m['polarity'] == '불만' else 'pos'] += 1
    if m['polarity'] == '불만': a['prods_neg'].add(m['product_key']); a['months'].add(m['month'])
    a['scope'] = m['aspect_scope']
out = []
for (cat, key), a in agg.items():
    lm = len(low_mention.get((cat, key), ()))
    out.append(dict(category=cat, need_key=key, scope=a['scope'], neg=a['neg'], pos=a['pos'], unresolved=round(a['neg'] / (a['neg'] + a['pos']), 2),
                    products_neg=f"{len(a['prods_neg'])}/{len(cat_prods[cat])}", months_neg=len(a['months']),
                    low_reviews_mentioning=lm, low_share=round(lm / cat_low_total[cat], 3) if cat_low_total[cat] else '',
                    population_share_pct=round(100 * lm / cat_site_total[cat], 3) if cat_site_total[cat] else '',
                    cat_low_total=cat_low_total[cat], cat_site_total=cat_site_total[cat]))
out.sort(key=lambda r: (r['category'], -r['neg']))
with open(f'{D}/metrics_by_category.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
for cat in sorted({r['category'] for r in out}):
    rows = [r for r in out if r['category'] == cat][:7]
    print(f"\n== {cat} (제품 {len(cat_prods[cat])}, ≤2★전수 {cat_low_total[cat]}, 사이트리뷰 {cat_site_total[cat]})")
    for r in rows: print(f"  {r['need_key']:8s} {r['scope'][:3]:3s} 불만{r['neg']:4d} 만족{r['pos']:4d} 미해결{r['unresolved']:.2f} 제품{r['products_neg']:5s} ≤2★언급{r['low_reviews_mentioning']:4d} 저평점내{str(r['low_share']):6s} 모집단{str(r['population_share_pct']):6s}%")
