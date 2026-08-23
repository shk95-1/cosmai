"""파생 테이블 예시: rank_daily = (source, board, category_key, product_key, day_kst) → n, rank_mean, rank_min, rank_max, present_share, price_mode. 출력: rank_daily.csv"""
import sqlite3, csv, collections, statistics, os
from datetime import datetime
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
day = lambda ts: datetime.fromtimestamp(datetime.fromisoformat(ts.replace('+00', '+00:00')).timestamp() + 9 * 3600).strftime('%Y-%m-%d')
g = collections.defaultdict(list); snaps = collections.defaultdict(set)
for s, b, c, pk, r, ts, pr in db.execute("select source,board,category_key,product_key,rank,captured_at,price from rank_snapshot"):
    d = day(ts); g[(s, b, c, pk, d)].append((int(r), pr)); snaps[(s, b, c, d)].add(ts)
rows = []
for (s, b, c, pk, d), v in sorted(g.items()):
    rs = [x[0] for x in v]; ps = [x[1] for x in v if x[1]]
    rows.append(dict(source=s, board=b, category_key=c, product_key=pk, day_kst=d, n=len(rs), n_snapshots=len(snaps[(s, b, c, d)]), present_share=round(len(rs) / len(snaps[(s, b, c, d)]), 2),
                     rank_mean=round(statistics.mean(rs), 1), rank_min=min(rs), rank_max=max(rs), price_mode=collections.Counter(ps).most_common(1)[0][0] if ps else ''))
with open(f'{D}/rank_daily.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print('rank_daily rows', len(rows), 'vs rank_snapshot', db.execute('select count(*) from rank_snapshot').fetchone()[0])
