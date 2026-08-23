"""Q4 가격→순위. (a) oliveyoung 가격 변화 이벤트: 변화 직전 6h 평균 순위 vs 이후 6/12/24h 평균 순위 (보드별, 미등장 시 101).
(b) 이름에 1+1/기획/올영픽/더블 포함 제품 vs 아닌 제품: 순위 분포·제품별 순위 표준편차. (c) sale 보드 첫 진입 시점 전후의 카테고리 보드 순위.
출력: price_rank_events.csv, promo_name_stats.csv, sale_entry_events.csv"""
import sqlite3, csv, collections, statistics, os, re
from datetime import datetime
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
H = lambda ts: int(datetime.fromisoformat(ts.replace('+00', '+00:00')).timestamp() // 3600)
rk = collections.defaultdict(dict); unit_hours = collections.defaultdict(set); names = {}
for b, c, pk, r, ts, nm in db.execute("select board,category_key,product_key,rank,captured_at,product_name from rank_snapshot where source='oliveyoung'"):
    rk[(b, pk)][H(ts)] = int(r); unit_hours[b].add(H(ts)); names[pk] = nm
def win(b, pk, h0, h1, pen=101):
    """보드 스냅샷이 있는 시각만 집계, 미등장은 pen"""
    hs = [h for h in unit_hours[b] if h0 <= h < h1]
    if not hs: return None, 0
    return statistics.mean(rk[(b, pk)].get(h, pen) for h in hs), len(hs)
# (a) 가격 이벤트
pp = collections.defaultdict(list)
for pk, ts, price, dr in db.execute("select product_key,captured_at,price,discount_rate from price_point where source='oliveyoung' order by captured_at"): pp[pk].append((H(ts), int(price), dr))
events = []
for pk, seq in pp.items():
    for (h0, p0, d0), (h1, p1, d1) in zip(seq, seq[1:]):
        if p0 == p1: continue
        for b in {b for (b, k) in rk if k == pk}:
            pre, npre = win(b, pk, h1 - 6, h1); post6, n6 = win(b, pk, h1, h1 + 6); post12, _ = win(b, pk, h1, h1 + 12); post24, n24 = win(b, pk, h1, h1 + 24)
            if pre is None or post6 is None: continue
            events.append(dict(product_key=pk, name=names[pk][:60], board=b, t_change=datetime.utcfromtimestamp(h1 * 3600).isoformat(), price_before=p0, price_after=p1, pct=round((p1 - p0) / p0 * 100, 1),
                               direction='drop' if p1 < p0 else 'rise', gap_h=h1 - h0, rank_pre6=round(pre, 1), rank_post6=round(post6, 1), rank_post12=round(post12, 1) if post12 else '', rank_post24=round(post24, 1) if post24 else '',
                               d6=round(post6 - pre, 1), d12=round(post12 - pre, 1) if post12 else '', d24=round(post24 - pre, 1) if post24 else '', n_pre=npre, n_post24=n24))
def w(fn, rs):
    with open(f'{D}/{fn}', 'w', newline='', encoding='utf-8') as f:
        wr = csv.DictWriter(f, fieldnames=list(rs[0].keys())); wr.writeheader(); wr.writerows(rs)
w('price_rank_events.csv', events)
print('price events (product×board):', len(events), collections.Counter(e['direction'] for e in events), 'products', len({e['product_key'] for e in events}))
for d in ('drop', 'rise'):
    es = [e for e in events if e['direction'] == d]
    for k in ('d6', 'd12', 'd24'):
        v = [e[k] for e in es if e[k] != '']
        print(f'  {d} {k}: n={len(v)} mean={statistics.mean(v):.1f} median={statistics.median(v):.1f} improved(<-3)={sum(1 for x in v if x < -3)} worsened(>3)={sum(1 for x in v if x > 3)}')
    big = [e for e in es if abs(e['pct']) >= 10]
    v = [e['d24'] for e in big if e['d24'] != '']
    if v: print(f'  {d} |pct|>=10: n={len(v)} mean d24={statistics.mean(v):.1f} median={statistics.median(v):.1f}')
# 대조군: 가격 변화 없는 제품의 임의 시각 6h-전후 차이 분포 (노이즈 기준선)
import random; random.seed(5); ctrl = []
nochg = [pk for pk, seq in pp.items() if len({p for _, p, _ in seq}) == 1]
for pk in random.sample(nochg, 400):
    for b in {b for (b, k) in rk if k == pk}:
        hs = sorted(rk[(b, pk)]); 
        if len(hs) < 30: continue
        h1 = random.choice(hs[6:-24]) if len(hs) > 30 else hs[10]
        pre, _ = win(b, pk, h1 - 6, h1); p24, _ = win(b, pk, h1, h1 + 24)
        if pre and p24: ctrl.append(p24 - pre)
print(f'  control (no price change) d24: n={len(ctrl)} mean={statistics.mean(ctrl):.1f} sd={statistics.pstdev(ctrl):.1f} p10={sorted(ctrl)[len(ctrl)//10]:.1f} p90={sorted(ctrl)[len(ctrl)*9//10]:.1f}')
# (b) 프로모션 이름
PROMO = re.compile(r'1\+1|기획|올영픽|더블|증정|한정|특가')
stats = []
for b in sorted(unit_hours):
    grp = collections.defaultdict(list)
    for (bb, pk), d in rk.items():
        if bb != b: continue
        v = list(d.values()); promo = bool(PROMO.search(names[pk]))
        grp[promo].append((statistics.mean(v), statistics.pstdev(v) if len(v) > 1 else 0, len(v), min(v)))
    for promo, lst in grp.items():
        stats.append(dict(board=b, promo_name=int(promo), n_products=len(lst), rank_mean=round(statistics.mean(x[0] for x in lst), 1), best_rank_mean=round(statistics.mean(x[3] for x in lst), 1),
                          top20_share=round(sum(1 for x in lst if x[0] <= 20) / len(lst), 3), snapshots_mean=round(statistics.mean(x[2] for x in lst), 1), rank_sd_mean=round(statistics.mean(x[1] for x in lst), 1)))
w('promo_name_stats.csv', sorted(stats, key=lambda r: (r['board'], r['promo_name'])))
tot = collections.defaultdict(list)
for r in stats: tot[r['promo_name']].append(r)
for p, rs in tot.items(): print('promo' if p else 'plain', 'n=', sum(r['n_products'] for r in rs), 'rank_mean', round(statistics.mean(r['rank_mean'] for r in rs), 1), 'top20_share', round(statistics.mean(r['top20_share'] for r in rs), 3), 'rank_sd', round(statistics.mean(r['rank_sd_mean'] for r in rs), 1), 'snapshots', round(statistics.mean(r['snapshots_mean'] for r in rs), 1))
# (c) sale 보드 진입
sale_hours = sorted(unit_hours['sale']); first_sale = min(sale_hours)
se = []
for (b, pk), d in rk.items():
    if b != 'sale': continue
    h_in = min(d)
    if h_in <= first_sale + 1: continue   # 창 시작부터 있던 것 제외
    for cb in {bb for (bb, k) in rk if k == pk and bb != 'sale'}:
        pre, npre = win(cb, pk, h_in - 12, h_in); post, npost = win(cb, pk, h_in, h_in + 12)
        if pre is None or post is None or npre < 4 or npost < 4: continue
        se.append(dict(product_key=pk, name=names[pk][:60], sale_entry=datetime.utcfromtimestamp(h_in * 3600).isoformat(), sale_rank_at_entry=d[h_in], cat_board=cb, cat_rank_pre12=round(pre, 1), cat_rank_post12=round(post, 1), d12=round(post - pre, 1), promo_name=int(bool(PROMO.search(names[pk])))))
if se:
    w('sale_entry_events.csv', se); v = [e['d12'] for e in se]
    print('sale entries:', len(se), 'products', len({e['product_key'] for e in se}), f'cat-board d12 mean={statistics.mean(v):.1f} median={statistics.median(v):.1f} improved(<-3)={sum(1 for x in v if x < -3)} worsened(>3)={sum(1 for x in v if x > 3)}')
