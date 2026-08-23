import csv,re,random,sys
sys.path.insert(0,'.')
csv.field_size_limit(10**9)
random.seed(42)
import importlib.util
# reuse regex from linker
src=open('link_brands.py').read()
ns={}
exec(src.split("vids={r['video_id']")[0].replace("if '--stop' in sys.argv","if True"),ns)
RX=ns['RX']; lower=ns['lower']; PW=ns['PW']; COOC=ns['COOC']
men=[r for r in csv.DictReader(open('brand_mentions.csv')) if r['src']!='title']
# 브랜드 단위로 고르게: 서로 다른 브랜드 60개에서 각 1건 (상위 브랜드 편중 방지 + 상위 20개는 반드시 포함)
bybrand={}
for r in men: bybrand.setdefault(r['brand'],[]).append(r)
top=[b for b,_ in sorted(bybrand.items(),key=lambda x:-len(x[1]))]
chosen=top[:20]+random.sample(top[20:],40)
tr={r['video_id']:r['full_text'] for r in csv.DictReader(open('data/transcripts.csv'))}
cm={r['comment_id']:r['text'] for r in csv.DictReader(open('data/comments.csv'))}
rows=[]
for b in chosen:
    r=random.choice(bybrand[b])
    t=tr[r['ref_id']] if r['src']=='transcript' else cm[r['ref_id']]
    ms=[m for m in RX.finditer(t) if lower[m.group(1).lower()]==b]
    if b in COOC: ms=[m for m in ms if PW.search(t[max(0,m.start()-25):m.end()+25])]
    m=random.choice(ms)
    rows.append([b,r['src'],r['ref_id'],t[max(0,m.start()-60):m.end()+60].replace('\n',' '),''])
with open('precision_sample60.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['brand','src','ref_id','context','label']); w.writerows(rows)
for r in rows: print(r[0],'|',r[1],'|',r[3])
