"""제품 단위 링킹 시도: rank_snapshot.product_name → (brand, 핵심 토큰 2개). 텍스트에서 브랜드 매치 뒤 60자 창 안에 핵심 토큰이 모두 있으면 히트."""
import csv, re, collections, random, sys
csv.field_size_limit(10**9)
random.seed(3)
lex={r['surface']:r['canonical'] for r in csv.DictReader(open('brand_lexicon.csv'))}
latest={}
for r in csv.DictReader(open('data/rank_snapshot.csv')):
    if r['source'] not in ('oliveyoung','glowpick'): continue
    k=(r['source'],r['product_key'])
    if k not in latest or r['captured_at']>latest[k]['captured_at']: latest[k]=r
GENERIC=set('기획 단품 세트 증정 한정 리필 더블 택1 본품 증정 단독 신상 신제품 올영 올리브영 NEW PICK 추가 구성 대용량 리뉴얼 에디션 콜라보 특가 할인 무료배송 사은품 1+1 2개 3개 2종 3종 4종 5종 6종 택 골라담기 세럼 크림 토너 앰플 에센스 로션 팩 마스크 패드 선크림 쿠션 틴트 립 클렌징 폼 오일 밤 워터 밀크 젤 스틱 미스트 샴푸 트리트먼트 바디 워시 핸드 파운데이션 컨실러 파우더 섀도우 팔레트 블러셔 마스카라 아이라이너 브로우 펜슬 립스틱 글로스 컬러 호 색상 향 향수 EDP EDT 스킨 케어 수분 보습 진정 장벽 트러블 미백 탄력 주름 모공 각질 톤업 커버 매트 글로우 워터프루프 약산성 저자극 데일리 라이트 리치 프로 플러스 베이직 오리지널 클래식 남성 여성 남자 여자 성인 어린이 아기 멀티 올인원 페이셜 페이스 바디 헤어 두피 코 눈 입술 손 발'.split())
def tokens(name,brand):
    n=re.sub(r'\[.*?\]|\(.*?\)','',name)
    n=re.sub(r'\d+(\.\d+)?\s?(ml|mL|ML|g|G|매|개|입|호|종|회분|mm|cm|%)','',n)
    n=n.replace(brand,' ')
    toks=[t for t in re.findall(r'[가-힣A-Za-z0-9]+',n) if len(t)>=2 and t not in GENERIC and not t.isdigit() and not re.fullmatch(r'[A-Za-z]{1,2}',t)]
    return toks[:2]
prods=[]
for (s,pk),r in latest.items():
    b=lex.get(r['brand'],r['brand'])
    if not b: continue
    t=tokens(r['product_name'],r['brand'])
    prods.append(dict(source=s,product_key=pk,brand=b,product_name=r['product_name'],tokens=t,rank=r['rank'],board=r['board']))
print('products',len(prods),'with >=2 tokens',sum(1 for p in prods if len(p['tokens'])>=2),'with 1 token',sum(1 for p in prods if len(p['tokens'])==1))
docs=[('transcript',r['video_id'],r['full_text']) for r in csv.DictReader(open('data/transcripts.csv'))]
docs+=[('comment',r['comment_id'],r['text']) for r in csv.DictReader(open('data/comments.csv'))]
# brand index: 브랜드별 제품 목록
byb=collections.defaultdict(list)
for p in prods:
    if p['tokens']: byb[p['brand']].append(p)
brx=re.compile(r'(?<![가-힣A-Za-z0-9])('+'|'.join(re.escape(b) for b in sorted(byb,key=len,reverse=True))+')')
hits=collections.Counter(); hits_full=collections.Counter(); ex=collections.defaultdict(list); doc_hits=collections.Counter()
for src,ref,t in docs:
    for m in brx.finditer(t):
        b=m.group(1); win=t[m.end():m.end()+60]
        for p in byb[b]:
            tk=p['tokens']
            if all(x.lower() in win.lower() for x in tk):
                key=(p['source'],p['product_key'])
                if len(tk)>=2: hits_full[key]+=1
                else: hits[key]+=1
                doc_hits[src]+=1
                if len(ex[key])<3: ex[key].append((src,t[max(0,m.start()-20):m.end()+70].replace('\n',' ')))
pk={(p['source'],p['product_key']):p for p in prods}
print('doc-level hits by src:',dict(doc_hits))
print('products with >=1 hit (2-token):',len(hits_full),'/',sum(1 for p in prods if len(p['tokens'])>=2),'; (1-token only):',len(hits),'/',sum(1 for p in prods if len(p['tokens'])==1))
print('products with >=5 hits (2-token):',sum(1 for k,v in hits_full.items() if v>=5))
rows=[]
for k,v in hits_full.most_common():
    p=pk[k]; rows.append(dict(source=p['source'],brand=p['brand'],product_name=p['product_name'],tokens=' '.join(p['tokens']),rank=p['rank'],board=p['board'],hits=v,example=ex[k][0][1] if ex[k] else ''))
with open('product_link_hits.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print('\nTop 20 products by hits:')
for r in rows[:20]: print(f"  {r['hits']:4d} | {r['brand']} | {r['tokens']} | {r['product_name'][:50]}")
print('\nRandom 20 hit examples (2-token) for precision check:')
keys=list(hits_full); random.shuffle(keys)
for k in keys[:20]:
    p=pk[k]; print(f"  [{p['brand']} / {' '.join(p['tokens'])}] {ex[k][0][1][:110]}")
