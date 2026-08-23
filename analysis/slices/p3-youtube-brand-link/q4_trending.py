import csv, re, collections, sys
csv.field_size_limit(10**9)
ns={}
exec(open('link_brands.py').read().split("vids={r['video_id']")[0].replace("if '--stop' in sys.argv","if True"),ns)
RX=ns['RX']; PW=ns['PW']; lower=ns['lower']; STOP=ns['STOP']
tr={r['video_id'] for r in csv.DictReader(open('data/transcripts.csv'))}
snap={r['video_id'] for r in csv.DictReader(open('data/videos.csv'))}
men=collections.defaultdict(set)
for r in csv.DictReader(open('brand_mentions.csv')):
    if r['src']!='comment' and r['brand']!='올리브영': men[r['video_id']].add(r['brand'])
BEAUTY=re.compile(r'화장|뷰티|메이크업|피부|스킨케어|코스메틱|올리브영|'+PW.pattern)
le=collections.defaultdict(dict)
for r in csv.DictReader(open('data/listing_entries.csv')):
    le[r['kind']][r['video_id']]=r
for kind,d in le.items():
    n=len(d); inb=sum(1 for v in d if v in men); bt=sum(1 for v,r in d.items() if BEAUTY.search(r['title']) or RX.search(r['title']))
    has_tr=sum(1 for v in d if v in tr); has_snap=sum(1 for v in d if v in snap)
    print(f'{kind}: {n} distinct videos; in video_snapshots {has_snap}; with transcript {has_tr}; title beauty-word/brand {bt} ({bt/n:.0%}); brand mention in title/transcript {inb} ({inb/n:.0%})')
d=le['trending.videos']
print('trending fetch range:', min(r['fetched_at'] for r in d.values())[:10], max(r['fetched_at'] for r in d.values())[:10], '; target:', set(r['target'] for r in d.values()))
import random; random.seed(1)
print('sample trending titles:')
for r in random.sample(list(d.values()),12): print('  -',r['title'][:70],'|',r['channel'])
