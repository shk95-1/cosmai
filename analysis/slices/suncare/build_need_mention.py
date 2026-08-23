"""3단계: product_ref(사이트 간 제품 묶기) + need_mention + 4지표. 출력: CSV 3개 + slice.db(SQLite)."""
import csv, re, sys, sqlite3, collections, statistics
D = sys.argv[1]
prods = list(csv.DictReader(open(f'{D}/products.csv', encoding='utf-8')))
cands = list(csv.DictReader(open(f'{D}/candidates_polarity.csv', encoding='utf-8')))

# ---------- product_ref ----------
STOP = re.compile(r'\[.*?\]|\(.*?\)|SPF\S*|PA\+*|\d+ml|\d+g|기획|본품|리필|단품|세트|더블|\+|/|·|,')
def tokens(name):
    n = STOP.sub(' ', name); n = re.sub(r'\s+', ' ', n)
    return {t for t in re.split(r'\s', n) if len(t) >= 2}
oy = [p for p in prods if p['source'] == 'oliveyoung']
refs = {}          # (source, product_key) -> ref_id
ref_rows = {}      # ref_id -> dict
def add(ref_id, p, role):
    refs[(p['source'], p['product_key'])] = ref_id
    row = ref_rows.setdefault(ref_id, dict(product_ref=ref_id, brand=p['brand'], name='', members=[], first_seen=None))
    row['members'].append(f"{p['source']}:{p['product_key']}")
    if role == 'primary' or not row['name']: row['name'] = STOP.sub('', p['product_name']).strip()[:50]
    fs = min(x for x in [p['first_ranked'], p['review_from']] if x) if (p['first_ranked'] or p['review_from']) else None
    if fs and (row['first_seen'] is None or fs < row['first_seen']): row['first_seen'] = fs
reviewed = [p for p in prods if int(p['reviews_collected'] or 0) > 0]
matches = []
for p in reviewed:
    if p['source'] == 'oliveyoung':
        add(f"oy:{p['product_key']}", p, 'primary'); continue
    # glowpick/daisomall → oliveyoung 후보: 같은 브랜드 + 토큰 2개 이상 공유
    tk = tokens(p['product_name']); best = None
    for q in oy:
        if (q['brand'] or '') != (p['brand'] or ''): continue
        shared = tk & tokens(q['product_name'])
        shared = {s for s in shared if s not in ('선크림', '수분', '진정')} or shared
        score = len(shared)
        if score >= 2 and (best is None or score > best[0]): best = (score, q, shared)
    if best:
        q = best[1]; rid = f"oy:{q['product_key']}"
        if rid not in ref_rows: add(rid, q, 'primary')
        add(rid, p, 'member'); matches.append((p['product_name'][:40], '→', q['product_name'][:50], best[2]))
    else:
        add(f"{p['source'][:2]}:{p['product_key']}", p, 'primary')
print('사이트 간 매핑:'); [print('  ', *m) for m in matches]
print('product_ref 수:', len(ref_rows), '(리뷰 제품 19 →)')

# ---------- need_mention ----------
nm = []
for i, c in enumerate(cands):
    if c['src'] not in ('review', 'yt_comment'): continue
    if not c['aspect'] or c['polarity'] not in ('불만', '만족'): continue
    ref = refs.get((c['site'], c['ref'].split('/')[0])) if c['src'] == 'review' else None
    strength = None
    if c['src'] == 'review' and c['weight']: strength = round(1 - float(c['weight']) / 5, 2)   # 평점 1 → 0.8, 5 → 0
    elif c['src'] == 'yt_comment' and c['weight']: strength = int(c['weight'])                    # 공감수
    nm.append(dict(mention_id=i, src=c['src'], site=c['site'], product_ref=ref or '', need_key=c['aspect'],
                   polarity=c['polarity'], strength=strength, observed_at=c['observed_at'], month=c['observed_at'][:7],
                   text_ref=c['ref'], sentence=c['sentence']))
print('need_mention:', len(nm), collections.Counter((m['src'], m['polarity']) for m in nm))

# ---------- 4지표 (리뷰 기준 = 제품 매핑 있음; 댓글은 보조) ----------
rv = [m for m in nm if m['src'] == 'review']
yc = [m for m in nm if m['src'] == 'yt_comment']
tot_neg = sum(1 for m in rv if m['polarity'] == '불만')
months_all = {m['month'] for m in rv}; prods_all = {m['product_ref'] for m in rv if m['product_ref']}
SITE_TOPIC_COVERS = {'발림텍스처', '자극따가움'}   # oliveyoung review_topic 축: 발림성/자극도/피부타입
new_refs = {r for r, row in ref_rows.items() if row['first_seen'] and row['first_seen'] >= '2026-01-01'}
metrics = []
for key in sorted({m['need_key'] for m in nm}):
    neg = [m for m in rv if m['need_key'] == key and m['polarity'] == '불만']
    pos = [m for m in rv if m['need_key'] == key and m['polarity'] == '만족']
    yneg = [m for m in yc if m['need_key'] == key and m['polarity'] == '불만']
    ypos = [m for m in yc if m['need_key'] == key and m['polarity'] == '만족']
    n, p = len(neg), len(pos)
    share = n / tot_neg if tot_neg else 0
    strength = statistics.mean(m['strength'] for m in neg) if neg else None
    low2 = sum(1 for m in neg if m['strength'] is not None and m['strength'] >= 0.6) / n if n else None   # 평점≤2 비율
    ylike = statistics.mean(m['strength'] for m in yneg) if yneg else None
    months_neg = {m['month'] for m in neg}; prods_neg = {m['product_ref'] for m in neg if m['product_ref']}
    persist_m = len(months_neg) / len(months_all) if months_all else 0
    persist_p = len(prods_neg) / len(prods_all) if prods_all else 0
    unresolved = n / (n + p) if n + p else None
    new_neg = [m for m in neg if m['product_ref'] in new_refs]; new_pos = [m for m in pos if m['product_ref'] in new_refs]
    unresolved_new = len(new_neg) / (len(new_neg) + len(new_pos)) if new_neg or new_pos else None
    metrics.append(dict(need_key=key, neg=n, pos=p, yt_neg=len(yneg), yt_pos=len(ypos),
        share_of_complaints=round(share, 3), strength_mean=round(strength, 2) if strength is not None else '',
        strength_low_rating_ratio=round(low2, 2) if low2 is not None else '', yt_like_mean=round(ylike, 1) if ylike is not None else '',
        persist_months=f'{len(months_neg)}/{len(months_all)}', persist_months_ratio=round(persist_m, 2),
        persist_products=f'{len(prods_neg)}/{len(prods_all)}', persist_products_ratio=round(persist_p, 2),
        unresolved_ratio=round(unresolved, 2) if unresolved is not None else '',
        unresolved_ratio_new2026=round(unresolved_new, 2) if unresolved_new is not None else '',
        site_topic_covers=key in SITE_TOPIC_COVERS))
metrics.sort(key=lambda m: -m['neg'])

# ---------- write ----------
def wcsv(name, rows, fields=None):
    with open(f'{D}/{name}', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0].keys())); w.writeheader(); w.writerows(rows)
pr = [dict(product_ref=k, brand=v['brand'], name=v['name'], first_seen=v['first_seen'], members=';'.join(v['members'])) for k, v in ref_rows.items()]
wcsv('product_ref.csv', pr); wcsv('need_mention.csv', nm); wcsv('metrics.csv', metrics)
con = sqlite3.connect(f'{D}/slice.db')
for name, rows in [('product_ref', pr), ('need_mention', nm), ('metrics', metrics)]:
    con.execute(f'drop table if exists {name}'); cols = list(rows[0].keys())
    con.execute(f"create table {name} ({', '.join(cols)})")
    con.executemany(f"insert into {name} values ({','.join('?'*len(cols))})", [[r[c] for c in cols] for r in rows])
con.commit(); con.close()

print(f"\n4지표 (리뷰 불만 총 {tot_neg}건, 월 {len(months_all)}개, 제품 {len(prods_all)}개; yt 댓글은 보조)")
print(f"{'need_key':8s} {'불만':>4s} {'만족':>4s} {'yt불만':>5s} | {'점유율':>6s} {'강도':>5s} {'≤2점':>5s} {'yt공감':>6s} | {'지속(월)':>8s} {'지속(제품)':>9s} | {'미해결':>6s} {'26년신제품':>9s} {'사이트토픽':>6s}")
for m in metrics:
    print(f"{m['need_key']:8s} {m['neg']:4d} {m['pos']:4d} {m['yt_neg']:5d} | {m['share_of_complaints']:6.2f} {str(m['strength_mean']):>5s} {str(m['strength_low_rating_ratio']):>5s} {str(m['yt_like_mean']):>6s} | {m['persist_months']:>8s} {m['persist_products']:>9s} | {str(m['unresolved_ratio']):>6s} {str(m['unresolved_ratio_new2026']):>9s} {str(m['site_topic_covers']):>6s}")
