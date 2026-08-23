"""brand_lexicon.csv 생성: rank_snapshot.brand(4개 소스) → canonical + alias.
alias 출처: (a) 공백 제거/괄호 제거 변형, (b) 수동 영문 별칭 후보를 유튜브 제목·자막 동시출현으로 검증."""
import csv, re, collections, json
csv.field_size_limit(10**9)
rs=list(csv.DictReader(open('data/rank_snapshot.csv')))
src_brands=collections.defaultdict(set)
nprod=collections.defaultdict(set)
for r in rs:
    b=r['brand'].strip()
    if not b: continue
    src_brands[b].add(r['source']); nprod[b].add(r['product_name'])
brands=set(src_brands)

def strip_paren(b): return re.sub(r'\s*\(.*?\)\s*','',b).strip()
# canonical: 하위 브랜드("본셉 스킨케어", "VT COSMETICS", "줌 바이 정샘물"은 유지) → 첫 토큰이 브랜드면 그쪽으로
canon={}
for b in brands:
    c=strip_paren(b)
    toks=c.split()
    if len(toks)>1 and toks[0] in brands and len(toks[0])>=2 and toks[1] not in ('바이','by'):
        c=toks[0]
    if c.upper()=='VT COSMETICS': c='VT'
    if c.startswith('바이 ') and c[3:] in brands: c=c[3:]
    canon[b]=c

# 수동 영문/한글 별칭 후보 (검증 전)
CAND={
 '닥터지':['Dr.G','DR.G','닥터 지'],'토리든':['Torriden','TORRIDEN'],'아누아':['Anua','ANUA'],'코스알엑스':['COSRX','cosrx','코스 알엑스'],
 '라운드랩':['Round Lab','ROUND LAB','라운드 랩'],'메디힐':['Mediheal','MEDIHEAL'],'달바':['d\'Alba','dalba','DALBA'],'바이오던스':['Biodance','BIODANCE'],
 '스킨1004':['SKIN1004','Skin1004','스킨천사'],'조선미녀':['Beauty of Joseon','BEAUTY OF JOSEON'],'티르티르':['TIRTIR','Tirtir'],'에스트라':['Aestura','AESTURA'],
 '마녀공장':['Manyo','MANYO','Ma:nyo'],'아비브':['Abib','ABIB'],'이니스프리':['Innisfree','INNISFREE','이니스 프리'],'에뛰드':['Etude','ETUDE','에뛰드하우스'],
 '클리오':['Clio','CLIO'],'롬앤':['rom&nd','romand','ROMAND','ROM&ND'],'페리페라':['peripera','PERIPERA'],'헤라':['HERA','Hera'],'설화수':['Sulwhasoo','SULWHASOO'],
 '라네즈':['Laneige','LANEIGE'],'미샤':['Missha','MISSHA'],'넘버즈인':['numbuzin','NUMBUZIN','넘버즈 인'],'브링그린':['Bring Green','BRING GREEN','브링 그린'],
 '일리윤':['Illiyoon','ILLIYOON'],'센텔리안24':['Centellian24','센텔리안'],'닥터자르트':['Dr.Jart','Dr. Jart','DR.JART','닥터 자르트'],'라로슈포제':['La Roche-Posay','La Roche Posay','라로슈 포제'],
 '바닐라코':['Banila Co','banila co','BANILA CO'],'에스쁘아':['espoir','ESPOIR'],'웨이크메이크':['Wakemake','WAKEMAKE'],'무지개맨션':['Mujigae','무지개 맨션'],
 '어뮤즈':['Amuse','AMUSE'],'힌스':['hince','HINCE'],'데이지크':['dasique','DASIQUE'],'삐아':['bbia','BBIA'],'퓌':['fwee','FWEE'],'토니모리':['Tonymoly','TONYMOLY','토니 모리'],
 '홀리카홀리카':['Holika Holika','HOLIKA HOLIKA','홀리카'],'메디큐브':['Medicube','MEDICUBE'],'구달':['goodal','GOODAL'],'셀퓨전씨':['Cell Fusion C','CELL FUSION C','셀퓨전'],
 '더마펌':['Dermafirm','DERMAFIRM'],'비플레인':['beplain','BEPLAIN'],'아이소이':['isoi','ISOI'],'메이크프렘':['make p:rem','makeprem','MAKE P:REM'],
 '이즈앤트리':['Isntree','ISNTREE'],'더페이스샵':['The Face Shop','THEFACESHOP','페이스샵'],'네이처리퍼블릭':['Nature Republic'],'아이오페':['IOPE'],
 '마몽드':['Mamonde','MAMONDE'],'한율':['Hanyul','HANYUL'],'프리메라':['Primera','PRIMERA'],'에이지투웨니스':['AGE20\'s','AGE 20\'s','에이지 투웨니스'],
 '바이오힐보':['Bio-Heal Bo','바이오힐 보'],'웰라쥬':['Wellage','WELLAGE'],'리쥬란':['Rejuran','REJURAN'],'에이프릴스킨':['Aprilskin','APRILSKIN','에이프릴 스킨'],
 '투쿨포스쿨':['Too Cool For School','too cool for school','투쿨 포 스쿨'],'키스미':['KISSME','Kiss Me'],'유리아쥬':['Uriage','URIAGE'],'아벤느':['Avene','AVENE','Avène'],
 '비오템':['Biotherm'],'키엘':['Kiehl\'s','KIEHL\'S','Kiehls','키엘스'],'맥':['MAC','M.A.C'],'나스':['NARS'],'디올':['Dior','DIOR'],'샤넬':['CHANEL','Chanel'],
 '랑콤':['Lancome','LANCOME','Lancôme'],'에스티 로더':['Estee Lauder','Estée Lauder','ESTEE LAUDER','에스티로더'],'입생로랑 뷰티':['YSL','입생로랑','입생 로랑'],
 '클라랑스':['Clarins'],'시세이도':['Shiseido','SHISEIDO'],'니베아':['NIVEA','Nivea'],'바세린':['Vaseline','VASELINE'],'세타필':['Cetaphil','CETAPHIL'],
 '피지오겔':['Physiogel'],'유세린':['Eucerin','EUCERIN'],'닥터브로너스':['Dr.Bronner\'s','Dr. Bronner\'s','닥터 브로너스'],'이솝':['Aesop','AESOP'],
 '스킨푸드':['Skinfood','SKINFOOD'],'더샘':['The Saem','THE SAEM','더 샘'],'어퓨':['A\'pieu','APIEU'],'에뛰드하우스':[], '밀크터치':['Milk Touch','MILK TOUCH','밀크 터치'],
 '클리덤':[], '오브제':['OBge'],'비앤비':[],'셀리맥스':['Celimax','CELIMAX'],'더마토리':['Dermatory'],'그라운드플랜':['Ground Plan','그라운드 플랜'],
 '닥터포헤어':['Dr.FORHAIR','닥터 포헤어'],'모레모':['Moremo'],'쿤달':['Kundal','KUNDAL'],'아로마티카':['Aromatica','AROMATICA'],'미장센':['Mise en scene'],'미쟝센':['Mise en scene','미장센'],
 '라카':['LAKA','Laka'],'밈즈':['MEEMS'],'어바웃톤':['About Tone','ABOUT TONE','어바웃 톤'],'유이라':['Yuira'],'시드물':[],'파넬':['Panel'],'피지':['PIZZI'],
 '바이오더마':['Bioderma','BIODERMA'],'쎄라비':['CeraVe','CERAVE','세라비'],'가히':['KAHI'],'메디필':['Medi-Peel','MEDI-PEEL','메디 필'],'닥터디퍼런트':['Dr.Different','닥터 디퍼런트'],
 'VT':['브이티','VT코스메틱','VT 코스메틱','브이티 코스메틱'],'AHC':['에이에이치씨'],'CNP':['씨앤피','차앤박'],'3CE':['쓰리씨이','스리씨이'],'VDL':['브이디엘'],'SVR':['에스브이알'],'JM솔루션':['JM Solution','제이엠솔루션'],
 '에이바자르':['Avajar'],'마스크'[:0]:[],
}
CAND.pop('',None)
# 검증: 유튜브 제목+자막에서 (한글 브랜드 AND 별칭) 같은 영상에 나오거나, 별칭 단독이라도 제목에 ≥3회
vids=collections.defaultdict(str)
for r in csv.DictReader(open('data/videos.csv')): vids[r['video_id']]+=' '+r['title']
for r in csv.DictReader(open('data/transcripts.csv')): vids[r['video_id']]+=' '+r['full_text']
titles={r['video_id']:r['title'] for r in csv.DictReader(open('data/videos.csv'))}
def count_co(b,a):
    co=0; alone=0; tit=0
    pa=re.compile(re.escape(a),re.I)
    for v,t in vids.items():
        ha=bool(pa.search(t))
        if not ha: continue
        if b in t: co+=1
        else: alone+=1
        if pa.search(titles.get(v,'')): tit+=1
    return co,alone,tit
rows=[]; verified=collections.defaultdict(list); rejected=[]
for b,als in CAND.items():
    if b not in brands: rejected.append((b,'not-in-lexicon')); continue
    for a in als:
        co,alone,tit=count_co(b,a)
        if co>=1 or tit>=3:
            verified[b].append(a); rows.append((b,a,co,alone,tit,'ok'))
        else: rows.append((b,a,co,alone,tit,'drop'))
with open('alias_verification.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['brand','alias','videos_with_both','videos_alias_only','titles_with_alias','decision']); w.writerows(rows)
print('rejected brands (not in lexicon):',rejected)

out=[]
seen=set()
for b in sorted(brands):
    c=canon[b]
    als=set()
    if b!=c: als.add(b)
    if ' ' in c: als.add(c.replace(' ',''))
    als |= set(verified.get(b,[]))
    als.discard(c)
    out.append(dict(canonical=c, surface=b, aliases='|'.join(sorted(als)), sources='|'.join(sorted(src_brands[b])), n_products=len(nprod[b])))
with open('brand_lexicon.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['canonical','surface','aliases','sources','n_products']); w.writeheader(); w.writerows(out)
print(len(out),'rows; canonical',len(set(canon.values())),'; verified aliases',sum(len(v) for v in verified.values()))
