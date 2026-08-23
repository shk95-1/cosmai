"""Q5 화해 연령·피부별 보드 제품 → product_ref 경유로 oliveyoung/glowpick 순위 부착. 출력: hwahae_segment.csv"""
import sqlite3, csv, collections, statistics, os
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
mem = collections.defaultdict(dict)
for r in csv.DictReader(open(f'{D}/product_ref_member.csv')): mem[r['product_ref']].setdefault(r['source'], []).append(r['product_key'])
key2ref = {(s, k): ref for ref, d in mem.items() for s, ks in d.items() for k in ks}
acc = collections.defaultdict(list); leaf = {}; nsnap = collections.defaultdict(set)
for s, b, c, cn, pk, r, ts in db.execute("select source,board,category_key,category_name,product_key,rank,captured_at from rank_snapshot"):
    acc[(s, b, c, pk)].append(int(r)); leaf[(s, b, c, pk)] = cn; nsnap[(s, b, c)].add(ts)
out = []
hw = [(b, c, cn, pk, nm, br) for b, c, cn, pk, nm, br in db.execute("select distinct board,category_key,category_name,product_key,product_name,brand from rank_snapshot where source='hwahae'")]
for b, c, cn, pk, nm, br in hw:
    ranks = acc[('hwahae', b, c, pk)]; ref = key2ref.get(('hwahae', pk), '')
    row = dict(hw_board=b, hw_category=cn, product_key=pk, brand=br, name=nm, hw_rank_mean=round(statistics.mean(ranks), 1), hw_snapshots=len(ranks), product_ref=ref, oy='', gp='')
    if ref:
        oy = []
        for k in mem[ref].get('oliveyoung', []):
            for (s, bb, cc, kk), v in acc.items():
                if s == 'oliveyoung' and kk == k: oy.append((round(statistics.mean(v), 1), bb, leaf[(s, bb, cc, kk)].split('>')[-1].strip(), round(len(v) / len(nsnap[(s, bb, cc)]), 2)))
        row['oy'] = '; '.join(f'{bb}:{m}(cover {cv}, {lf})' for m, bb, lf, cv in sorted(oy))
        gp = []
        for k in mem[ref].get('glowpick', []):
            for (s, bb, cc, kk), v in acc.items():
                if s == 'glowpick' and kk == k: gp.append((round(statistics.mean(v), 1), leaf[(s, bb, cc, kk)]))
        row['gp'] = '; '.join(f'{lf}:{m}' for m, lf in sorted(gp))
    out.append(row)
out.sort(key=lambda r: (r['hw_board'], r['hw_rank_mean']))
with open(f'{D}/hwahae_segment.csv', 'w', newline='', encoding='utf-8') as f:
    wr = csv.DictWriter(f, fieldnames=list(out[0].keys())); wr.writeheader(); wr.writerows(out)
for r in out: print(r['hw_board'], r['hw_rank_mean'], r['brand'], r['name'][:30], '|', r['product_ref'], '|', r['oy'][:120], '|', r['gp'])
print('hwahae products', len({r['product_key'] for r in out}), 'with ref', len({r['product_key'] for r in out if r['product_ref']}))
