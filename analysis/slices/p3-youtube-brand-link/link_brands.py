"""브랜드 링커 v1: 경계 규칙 + 조사 허용. 스톱리스트 없이 모든 후보를 찍고 통계(stats_brand.csv)를 낸다.
출력: mentions_raw.csv (src, ref_id, video_id, brand, count, published_at, cooc_count)"""
import csv, re, collections, sys, json
csv.field_size_limit(10**9)
lex=list(csv.DictReader(open('brand_lexicon.csv')))
surf2canon={}
for r in lex:
    surf2canon[r['canonical']]=r['canonical']
    for a in r['aliases'].split('|'):
        if a: surf2canon[a]=r['canonical']
STOP=set(); COOC=set()
if '--stop' in sys.argv:
    for r in csv.DictReader(open('stoplist.csv')):
        if r['tier']=='stop': STOP.add(r['brand'])
        elif r['tier']=='cooc': COOC.add(r['brand'])
surfaces=[s for s in surf2canon if surf2canon[s] not in STOP]
surfaces.sort(key=len, reverse=True)
PART=r'(?:이에요|예요|이고|이랑|이라고|이라는|이라서|이니까|인데|입니다|이야|이죠|이네|으로|에서|처럼|보다|밖에|부터|까지|는|은|이|가|을|를|도|의|로|와|과|에|랑|만|나|든|요|야|죠|네|거|꺼|건|껀|게|께)?'
PROD=r'(?:크림|세럼|팩|선크림|립|쿠션|토너|앰플|에센스|클렌징|마스크|패드|틴트|로션|파데|파운데이션|스킨|폼|샴푸|미스트|밤|오일|컨실러|섀도우|블러셔|마스카라|아이라이너|선스틱|젤|스틱|바디워시|핸드크림|클렌저|펜슬|브로우|립스틱|글로스|팔레트|선쿠션|톤업|기획|세트)'
alt='|'.join(re.escape(s) for s in surfaces)
RX=re.compile(r'(?<![가-힣A-Za-z0-9])('+alt+r')'+PART+r'(?=$|[^가-힣A-Za-z0-9]|'+PROD+')', re.I)
# 제품어 공기(共起) 창: ±25자
PW=re.compile(PROD)
lower={s.lower():c for s,c in surf2canon.items()}
def link(text):
    out=collections.Counter(); co=collections.Counter()
    for m in RX.finditer(text):
        c=lower[m.group(1).lower()]
        w=text[max(0,m.start()-25):m.end()+25]
        hit=bool(PW.search(w))
        if c in COOC and not hit: continue
        out[c]+=1
        if hit: co[c]+=1
    return out,co
vids={r['video_id']:r for r in csv.DictReader(open('data/videos.csv'))}
rows=[]
def emit(src,ref,vid,cnt,co,pub):
    for b,n in cnt.items(): rows.append((src,ref,vid,b,n,co[b],pub))
for v,r in vids.items():
    c,co=link(r['title']); emit('title',v,v,c,co,r['published_at'])
for r in csv.DictReader(open('data/transcripts.csv')):
    c,co=link(r['full_text']); emit('transcript',r['video_id'],r['video_id'],c,co,vids.get(r['video_id'],{}).get('published_at',''))
for r in csv.DictReader(open('data/comments.csv')):
    c,co=link(r['text']); emit('comment',r['comment_id'],r['video_id'],c,co,r['published_at'])
fn='brand_mentions.csv' if STOP else 'mentions_raw.csv'
with open(fn,'w',newline='') as f:
    w=csv.writer(f); w.writerow(['src','ref_id','video_id','brand','count','cooc_count','published_at']); w.writerows(rows)
# 브랜드별 통계
st=collections.defaultdict(lambda: collections.Counter())
for src,ref,vid,b,n,co,pub in rows:
    st[b][src+'_docs']+=1; st[b][src+'_hits']+=n; st[b][src+'_cooc']+=co
with open('stats_brand.csv' if not STOP else 'stats_brand_final.csv','w',newline='') as f:
    cols=['brand','title_docs','transcript_docs','transcript_hits','transcript_cooc','comment_docs','comment_hits','comment_cooc','cooc_rate','title_ratio']
    w=csv.writer(f); w.writerow(cols)
    for b,c in sorted(st.items(), key=lambda x:-x[1]['transcript_docs']-x[1]['comment_docs']):
        hits=c['transcript_hits']+c['comment_hits']; cooc=c['transcript_cooc']+c['comment_cooc']
        w.writerow([b,c['title_docs'],c['transcript_docs'],c['transcript_hits'],c['transcript_cooc'],c['comment_docs'],c['comment_hits'],c['comment_cooc'],round(cooc/hits,3) if hits else '',round(c['title_docs']/max(1,c['transcript_docs']),3)])
print(fn, len(rows))
