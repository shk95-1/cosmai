"""_raw/*.csv → slice.db (SQLite). 읽기 전용 분석용 사본."""
import csv, sqlite3, os, subprocess
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
EXPORT = {'rank_snapshot': 'select * from trend_radar.rank_snapshot', 'price_point': 'select * from trend_radar.price_point',
          'product': 'select source,product_key,name,brand,volume,url,first_seen_at,last_seen_at,(ingredients is not null) has_ingr from trend_radar.product',
          'new_product': 'select * from trend_radar.new_product'}
os.makedirs(f'{D}/_raw', exist_ok=True)
for t, sql in EXPORT.items():   # _raw/*.csv 가 없으면 postgres에서 내보냄 (읽기 전용)
    if not os.path.exists(f'{D}/_raw/{t}.csv'):
        with open(f'{D}/_raw/{t}.csv', 'w') as f: subprocess.run(['docker', 'exec', 'shared-postgres', 'psql', '-U', 'platform', '-d', 'app', '-c', f'\\copy ({sql}) to stdout csv header'], stdout=f, check=True)
for t in ['rank_snapshot', 'price_point', 'product', 'new_product']:
    rows = list(csv.reader(open(f'{D}/_raw/{t}.csv', encoding='utf-8'))); hdr = rows[0]
    db.execute(f'drop table if exists {t}'); db.execute(f'create table {t} ({",".join(hdr)})')
    db.executemany(f'insert into {t} values ({",".join("?"*len(hdr))})', [[None if v == '' else v for v in r] for r in rows[1:]])
    print(t, len(rows) - 1)
db.execute('create index if not exists rs1 on rank_snapshot(source,board,category_key,captured_at)')
db.execute('create index if not exists rs2 on rank_snapshot(source,product_key)')
db.execute('create index if not exists pp1 on price_point(source,product_key,captured_at)')
db.commit()
