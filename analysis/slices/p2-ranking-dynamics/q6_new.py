"""Q6 new_product / is_new 제품이 창 안에서 랭킹에 들어왔는가. 출력: new_entry.csv"""
import sqlite3, csv, collections, os
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
print('new_product:', db.execute("select source, count(*), count(distinct product_key), min(captured_at), max(captured_at), min(listed_at), max(listed_at) from new_product group by 1").fetchall())
rs = db.execute("""select n.source, n.product_key, n.name, n.listed_at, min(r.captured_at), min(r.rank), count(r.rank), group_concat(distinct r.board)
                   from new_product n left join rank_snapshot r on r.source=n.source and r.product_key=n.product_key group by 1,2,3,4""").fetchall()
out = [dict(source=s, product_key=pk, name=nm, listed_at=la, first_ranked=fr or '', best_rank=br or '', n_rank_rows=n, boards=b or '') for s, pk, nm, la, fr, br, n, b in rs]
print('new_product in any ranking:', sum(1 for r in out if r['n_rank_rows']), '/', len(out), collections.Counter((r['source'], bool(r['n_rank_rows'])) for r in out))
ranked = [r for r in out if r['n_rank_rows']]
print('  boards of ranked new_product:', collections.Counter(b for r in ranked for b in r['boards'].split(',')))
print('  only sale_rising:', sum(1 for r in ranked if r['boards'] == 'sale_rising'), '| best_rank<=20:', sum(1 for r in ranked if int(r['best_rank']) <= 20), '| first_ranked at window start(18일 13:00):', sum(1 for r in ranked if r['first_ranked'] == '2026-08-18 13:00:00+00'))
print('  new_product.name 이 숫자(키와 동일)인 행:', sum(1 for r in out if r['name'] and r['name'].isdigit()), '/', len(out))
# is_new
print('is_new=t rows by source/board:', db.execute("select source, board, count(*), count(distinct product_key) from rank_snapshot where is_new='t' group by 1,2").fetchall())
print('is_new 제품의 보드 최초 등장 시각 분포(daisomall): ', db.execute("""select substr(fs,1,10), count(*) from (select product_key, min(captured_at) fs from rank_snapshot where source='daisomall' and product_key in (select product_key from rank_snapshot where is_new='t') group by 1) group by 1""").fetchall())
print('daisomall is_new 제품 중 창 시작(18일 13:00) 이후 처음 등장한 것:', db.execute("""select count(*) from (select product_key, min(captured_at) fs from rank_snapshot where source='daisomall' and product_key in (select product_key from rank_snapshot where is_new='t') group by 1) where fs > '2026-08-18 13:00:00+00'""").fetchall())
print('hwahae is_new:', db.execute("select board, product_name, min(captured_at), max(captured_at), min(rank) from rank_snapshot where source='hwahae' and is_new='t' group by 1,2").fetchall())
# 창 안에서 처음 등장한 제품(전 사이트): 첫 스냅샷 이후 새로 들어온 제품 수 (= 랭킹 진입), product.first_seen_at 기준
print('product.first_seen_at by day:', db.execute("select source, substr(first_seen_at,1,10) d, count(*) from product group by 1,2 order by 1,2").fetchall())
with open(f'{D}/new_entry.csv', 'w', newline='', encoding='utf-8') as f:
    wr = csv.DictWriter(f, fieldnames=list(out[0].keys())); wr.writeheader(); wr.writerows(out)
