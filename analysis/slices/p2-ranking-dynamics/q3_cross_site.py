"""Q3 사이트 간 랭킹 일치도. product_ref로 묶인 제품의 (glowpick 카테고리 순위) vs (oliveyoung 보드 내 순위, 창 평균) Spearman.
oy 순위는 보드(top-100) 기준 창 평균 순위; 보드에 없는 날은 101로 처리한 버전도 계산. hwahae/daisomall도 같은 방식.
출력: cross_site_corr.csv, cross_site_pairs.csv, lead_lag.csv"""
import sqlite3, csv, collections, statistics, os
from datetime import datetime
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
mem = collections.defaultdict(dict)   # ref -> {source: [keys]}
for r in csv.DictReader(open(f'{D}/product_ref_member.csv')): mem[r['product_ref']].setdefault(r['source'], []).append(r['product_key'])
key2ref = {(s, k): ref for ref, d in mem.items() for s, ks in d.items() for k in ks}
# 제품 × (source, board, category_key) 창 평균 순위 + 일별 평균
rows = db.execute("select source,board,category_key,category_name,product_key,rank,captured_at from rank_snapshot").fetchall()
acc = collections.defaultdict(list); daily = collections.defaultdict(lambda: collections.defaultdict(list)); nsnap = collections.defaultdict(set); catname = {}
for s, b, c, cn, pk, r, ts in rows:
    acc[(s, b, c, pk)].append(int(r)); nsnap[(s, b, c)].add(ts); catname[(s, b, c)] = cn
    day = (datetime.fromisoformat(ts.replace('+00', '+00:00')).timestamp() + 9 * 3600) // 86400
    daily[(s, b, c, pk)][day].append(int(r))
def spearman(x, y):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v); i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
            for k in range(i, j + 1): r[o[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry = rk(x), rk(y); mx, my = statistics.mean(rx), statistics.mean(ry)
    sx = sum((a - mx) ** 2 for a in rx) ** .5; sy = sum((a - my) ** 2 for a in ry) ** .5
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy) if sx and sy else None
R = lambda v: round(v, 3) if v is not None else ''
# oy 보드 내 leaf 카테고리 재순위: (board, leaf) 안에서 창 평균 순위 오름차순
leaf_of = {}
for s_, b_, c_, cn_, pk_, r_, ts_ in rows:
    if s_ == 'oliveyoung': leaf_of[(s_, b_, c_, pk_)] = cn_
leaf_rank = {}
for (s_, b_, c_, pk_), v in acc.items():
    if s_ == 'oliveyoung': leaf_rank.setdefault((s_, b_, c_, leaf_of[(s_, b_, c_, pk_)]), []).append((statistics.mean(v), pk_))
leaf_pos = {}
for k, lst in leaf_rank.items():
    for i, (m, pk_) in enumerate(sorted(lst)): leaf_pos[k[:3] + (pk_,)] = (i + 1, len(lst), k[3])
# 비앵커 사이트의 랭킹 단위마다: 그 단위에 있는 제품 중 ref가 있고 oy 보드에 있는 것
oy_units = sorted({(s, b, c) for (s, b, c, pk) in acc if s == 'oliveyoung'})
other_units = sorted({(s, b, c) for (s, b, c, pk) in acc if s != 'oliveyoung'})
out, pairs = [], []
for u in other_units:
    prods = [(pk, statistics.mean(v)) for (s, b, c, pk), v in acc.items() if (s, b, c) == u]
    for ou in oy_units:
        xs, ys, ys101, names, yleaf = [], [], [], [], []
        for pk, mr in prods:
            ref = key2ref.get((u[0], pk))
            if not ref or 'oliveyoung' not in mem[ref]: continue
            oy_ranks = [acc[ou + (k,)] for k in mem[ref]['oliveyoung'] if ou + (k,) in acc]
            if not oy_ranks: continue
            best = min(statistics.mean(v) for v in oy_ranks)             # 변형 SKU 중 가장 잘 팔린 것
            cover = max(len(v) for v in oy_ranks) / len(nsnap[ou])        # 보드 등장 비율
            pen = best * cover + 101 * (1 - cover)                        # 미등장 시간을 101위로
            bk = min(((statistics.mean(acc[ou + (k,)]), k) for k in mem[ref]['oliveyoung'] if ou + (k,) in acc))[1]
            lp = leaf_pos[ou + (bk,)]
            xs.append(mr); ys.append(best); ys101.append(pen); names.append((pk, ref)); yleaf.append(lp[0])
            pairs.append(dict(source=u[0], board=u[1], category_key=u[2], category_name=catname.get(u, ''), product_key=pk, product_ref=ref, rank_mean=round(mr, 1), oy_board=ou[1], oy_rank_mean=round(best, 1), oy_cover=round(cover, 2), oy_rank_pen=round(pen, 1), oy_leaf=lp[2], oy_leaf_rank=f'{lp[0]}/{lp[1]}'))
        if len(xs) >= 5:
            out.append(dict(source=u[0], board=u[1], category_key=u[2], category_name=catname.get(u, ''), oy_board=ou[1], n_shared=len(xs), n_in_unit=len(prods), spearman=round(spearman(xs, ys), 3), spearman_pen101=round(spearman(xs, ys101), 3), spearman_leaf_rerank=R(spearman(xs, yleaf)), n_leaf=len({leaf_pos[ou + (min(((statistics.mean(acc[ou + (k,)]), k) for k in mem[key2ref[(u[0], p)]]['oliveyoung'] if ou + (k,) in acc))[1],)][2] for p, _ in prods if key2ref.get((u[0], p)) and any(ou + (k,) in acc for k in mem[key2ref[(u[0], p)]].get('oliveyoung', []))})))
out.sort(key=lambda r: (r['source'], r['category_key'], -r['n_shared']))
def w(fn, rs):
    with open(f'{D}/{fn}', 'w', newline='', encoding='utf-8') as f:
        wr = csv.DictWriter(f, fieldnames=list(rs[0].keys())); wr.writeheader(); wr.writerows(rs)
w('cross_site_corr.csv', out); w('cross_site_pairs.csv', pairs)
for r in out: print(r)
# lead/lag: glowpick 일별 순위 변화 vs oy 일별 평균 순위 변화 (같은 ref), lag -1,0,+1 일
ll = []
for lag in (-2, -1, 0, 1, 2):
    xs, ys = [], []
    for (s, b, c, pk), dd in daily.items():
        if s != 'glowpick': continue
        ref = key2ref.get((s, pk))
        if not ref or 'oliveyoung' not in mem[ref]: continue
        for ou in oy_units:
            for k in mem[ref]['oliveyoung']:
                od = daily.get(ou + (k,))
                if not od: continue
                for day in dd:
                    if day + 1 in dd and day + lag in od and day + lag + 1 in od:
                        xs.append(statistics.mean(dd[day + 1]) - statistics.mean(dd[day])); ys.append(statistics.mean(od[day + lag + 1]) - statistics.mean(od[day + lag]))
    nz = [(a, b) for a, b in zip(xs, ys) if a != 0]
    ll.append(dict(lag_days_oy_vs_glowpick=lag, n=len(xs), n_glowpick_moved=len(nz), spearman_all=R(spearman(xs, ys)) if len(xs) > 5 else '', spearman_moved=R(spearman([a for a, b in nz], [b for a, b in nz])) if len(nz) > 5 else ''))
w('lead_lag.csv', ll); [print(r) for r in ll]
