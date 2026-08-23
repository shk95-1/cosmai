"""Q1 시간 그레인: 연속 스냅샷 간 top-20 교체율, |Δrank| 분포, 동일 스냅샷 비율, 시간대 패턴, 일 내/일 간 분산 분해.
랭킹 단위 = (source, board, category_key). 출력: rank_churn.csv, rank_churn_pairs.csv"""
import sqlite3, csv, statistics, collections, os
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
rows = db.execute("select source,board,category_key,captured_at,product_key,rank from rank_snapshot").fetchall()
snaps = collections.defaultdict(dict)   # (unit, ts) -> {pk: rank}
for s, b, c, ts, pk, r in rows: snaps[(s, b, c, ts)][pk] = int(r)
units = collections.defaultdict(list)
for (s, b, c, ts) in snaps: units[(s, b, c)].append(ts)
pairs = []; out = []
for u, tss in units.items():
    tss.sort(); K = 20 if u[0] != 'hwahae' else 9
    identical = 0; churns = []; deltas = []; gaps = []
    for a, b in zip(tss, tss[1:]):
        A, B = snaps[u + (a,)], snaps[u + (b,)]
        topA = {p for p, r in A.items() if r <= K}; topB = {p for p, r in B.items() if r <= K}
        churn = 1 - len(topA & topB) / max(len(topA | topB), 1)   # 1 - Jaccard (top-K)
        entered = len(topB - topA)
        common = [abs(A[p] - B[p]) for p in A if p in B]
        same = A == B
        identical += same
        mad = statistics.mean(common) if common else None
        churns.append(churn); deltas += common
        # 시간 간격(h)
        from datetime import datetime
        fa = datetime.fromisoformat(a.replace('+00', '+00:00')); fb = datetime.fromisoformat(b.replace('+00', '+00:00'))
        gap = (fb - fa).total_seconds() / 3600; gaps.append(gap)
        pairs.append(dict(source=u[0], board=u[1], category_key=u[2], t0=a, t1=b, gap_h=round(gap, 1), hour_kst=(fb.hour + 9) % 24,
                          topk=K, topk_entered=entered, topk_churn=round(churn, 3), mean_abs_delta=round(mad, 2) if mad is not None else '',
                          identical=int(same), n_common=len(common)))
    n = len(tss) - 1
    if n <= 0: continue
    d_sorted = sorted(deltas)
    out.append(dict(source=u[0], board=u[1], category_key=u[2], n_snapshots=len(tss), n_pairs=n, median_gap_h=round(statistics.median(gaps), 1),
                    identical_share=round(identical / n, 3), topk=K, topk_churn_mean=round(statistics.mean(churns), 3),
                    topk_churn_nonzero_share=round(sum(1 for c in churns if c > 0) / n, 3),
                    abs_delta_mean=round(statistics.mean(deltas), 2) if deltas else '', abs_delta_p50=d_sorted[len(d_sorted) // 2] if deltas else '',
                    abs_delta_p90=d_sorted[int(len(d_sorted) * .9)] if deltas else '', abs_delta_zero_share=round(sum(1 for d in deltas if d == 0) / len(deltas), 3) if deltas else ''))
out.sort(key=lambda r: (r['source'], r['board'], r['category_key']))
def w(fn, rs):
    with open(f'{D}/{fn}', 'w', newline='', encoding='utf-8') as f:
        wr = csv.DictWriter(f, fieldnames=list(rs[0].keys())); wr.writeheader(); wr.writerows(rs)
w('rank_churn.csv', out); w('rank_churn_pairs.csv', pairs)
print('units', len(out)); 
for r in out: 
    if r['source'] != 'glowpick' or r['category_key'] in ('41', '3'): print(r)
