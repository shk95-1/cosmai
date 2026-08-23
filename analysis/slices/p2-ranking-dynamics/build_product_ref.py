"""Q2 product_ref: 사이트 간 동일 제품 묶기. 규칙 = 브랜드 정규화(별칭 사전) 일치 + 이름 정규화 후 토큰 2개 이상 공유 또는 (토큰 1개 + 문자 바이그램 Dice≥0.6).
앵커 = oliveyoung 제품(최대 카탈로그). 비앵커 사이트끼리(glowpick↔hwahae↔daisomall)도 같은 규칙으로 묶는다.
출력: product_ref.csv, product_ref_member.csv, product_ref_candidates.csv(점수 포함, 검수용)"""
import sqlite3, re, csv, collections, os, itertools
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')

BRAND_ALIAS = {  # 정규화된 브랜드 → 정규화된 대표 브랜드 (손으로 확인한 것만)
    'vtcosmetics': 'vt', 'cnp': '차앤박', '프릴루드딘토': '딘토', '어퓨더퓨어': '어퓨', '바이리얼베리어': '리얼베리어', '애경바세린': '바세린',
    '본셉스킨케어': '본셉', '본셉메이크업': '본셉', '리더스코스메틱': '리더스', '미모바이마몽드': '마몽드', '줌바이정샘물': '정샘물',
    '플레이101by에뛰드': '에뛰드', '네이처리퍼블릭바이플라워': '네이처리퍼블릭', '네이처리퍼블릭식물원': '네이처리퍼블릭', '제이엠솔루션': 'jm솔루션',
    '드롭비컬러즈': '드롭비', '밀크터치디어씽': '밀크터치', '3m넥스케어': '넥스케어', '글린트바이비디보브': '글린트', '입생로랑뷰티': '입생로랑',
}
def norm_brand(b):
    b = re.sub(r'[\s™®\(\)\.\-_/]', '', (b or '').lower())
    return BRAND_ALIAS.get(b, b)

NOISE = re.compile(r'\[.*?\]|\(.*?\)|【.*?】|SPF\s*\d+\+*|PA\++|\d+(\.\d+)?\s*(ml|mL|g|kg|매입|매|입|개입|개|ea|EA|p|P|호|종|장|정|포|pcs|fl\.?\s*oz\.?|oz)(\b|(?=[^A-Za-z0-9]))|\b\d+\s*\+\s*\d+\b|\d+\s*colors?|\d+\s*색|\d+\s*종|x\s*\d+|X\s*\d+|\*\s*\d+|/\s*\d[\d\.]*\s*(fl\.?\s*oz|oz)', re.I)
WORDS = ['기획', '본품', '리필', '단품', '세트', '더블', '증정', '한정', '올영픽', '올영', '픽', '특가', '신상', 'NEW', '대용량', '업그레이드', '리뉴얼', '1+1', '2입', '3입', '2개입', '더블기획', '기획세트', '듀오', '구성', '랜덤', '택1', '택', '선택', '추가', '온라인', '단독', '공식', '정품', '국내', '수입', '용량', '사은품', '무료', '배송', 'pick', 'PICK', '한정판', '에디션', '리미티드', '패키지', '세일', '할인', '할인가']
SYN = [(r'썬', '선'), (r'쿠션팩트', '쿠션'), (r'폼클렌저|폼 클렌저|클렌징폼|클렌징 폼|포밍클렌저|폼 클렌징', '클렌징폼'), (r'크림\s*패드', '패드'), (r'수분크림', '수분 크림'), (r'오드퍼퓸|오 드 퍼퓸|오드 퍼퓸|EDP', '오드퍼퓸'), (r'오드뚜왈렛|오 드 뚜왈렛|오드 뚜왈렛|EDT', '오드뚜왈렛'), (r'마스크팩|마스크 팩', '마스크'), (r'시트마스크|시트 마스크', '마스크'), (r'선 크림', '선크림'), (r'썬크림', '선크림'), (r'토너패드|토너 패드', '패드'), (r'샴푸바', '샴푸'), (r'립 틴트', '립틴트'), (r'([가-힣])(\d)', r'\1 \2')]
def norm_name(name, brand, keep_bracket=False):
    n = name or ''
    if keep_bracket: n = re.sub(r'\[(SPF[^\]]*|PA[^\]]*)\]', ' ', n, flags=re.I); n = n.replace('[', ' ').replace(']', ' ')
    n = NOISE.sub(' ', n)
    n = re.sub(r'(?<=[가-힣A-Za-z])(기획|단품|세트|리필|더블|증정)(?![가-힣])', ' ', n)   # 붙어 쓴 마케팅어
    for w in WORDS: n = re.sub(r'(?<![A-Za-z가-힣])' + re.escape(w) + r'(?![A-Za-z가-힣])', ' ', n)
    for a, b in SYN: n = re.sub(a, b, n)
    n = n.lower()
    b = (brand or '').lower()
    if b: n = n.replace(b, ' '); n = n.replace(b.replace(' ', ''), ' ')
    n = re.sub(r'[^\w가-힣\.]+', ' ', n); n = re.sub(r'(?<!\d)\.|\.(?!\d)', ' ', n)   # 숫자 토큰(1025, 365, 2.0 …)은 제품 식별자로 유지
    return re.sub(r'\s+', ' ', n).strip()
STOP_TOK = {'크림', '세럼', '토너', '로션', '앰플', '에센스', '마스크', '패드', '샴푸', '선크림', '수분', '진정', '미스트', '클렌저', '젤', '오일', '밤', '팩', '워시', '바디', '헤어', '립', '페이셜', '스킨', '케어', 'the', '더', '데일리', '모이스처', '모이스쳐', '리페어', '트리트먼트', '에디션'}
def toks(n): return {t for t in n.split() if len(t) >= 2 or t.isdigit()}
def nums(n): return {t for t in n.split() if re.fullmatch(r'[\d\.]+', t)}
def sig_toks(n): return {t for t in toks(n) if t not in STOP_TOK}
def bigrams(n):
    c = n.replace(' ', ''); return {c[i:i + 2] for i in range(len(c) - 1)}
def dice(a, b): return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0

prods = []
for s, pk, name, brand, vol in db.execute("select source,product_key,name,brand,volume from product"):
    if not name or re.fullmatch(r'\d+', name): continue
    prods.append(dict(source=s, product_key=pk, name=name, brand=brand or '', nb=norm_brand(brand), volume=vol or ''))
# daisomall 브랜드 없음 → 이름 앞토막이 oy 브랜드면 부여
oy_brands = {p['brand']: p['nb'] for p in prods if p['source'] == 'oliveyoung'}
oy_brand_list = sorted(oy_brands, key=len, reverse=True)
inferred = 0
for p in prods:
    if p['nb']: continue
    head = re.sub(r'^\[.*?\]\s*', '', p['name'])
    for b in oy_brand_list:
        if len(b) >= 2 and head.startswith(b): p['nb'] = oy_brands[b]; p['brand_inferred'] = b; inferred += 1; break
for p in prods: p['nn'] = norm_name(p['name'], p.get('brand') or p.get('brand_inferred', ''), keep_bracket=p['source'] in ('glowpick', 'hwahae')); p['tk'] = toks(p['nn']); p['st'] = sig_toks(p['nn']); p['bg'] = bigrams(p['nn'])
print('products', len(prods), 'daiso brand inferred', inferred)

FORMS = ['크림미스트', '클렌징폼', '클렌징오일', '클렌징밀크', '클렌징워터', '클렌징밤', '클렌징젤', '클렌징티슈', '톤업선크림', '선크림', '선스틱', '선세럼', '선쿠션', '선스프레이', '선밤', '선젤', '선로션', '선에센스',
         '바디워시', '바디로션', '바디크림', '바디미스트', '바디오일', '바디스크럽', '바디버터', '핸드크림', '풋샴푸', '샴푸', '트리트먼트', '컨디셔너', '헤어오일', '헤어에센스', '헤어퍼퓸', '헤어미스트',
         '오드퍼퓸', '오드뚜왈렛', '퍼퓸', '쿠션', '파운데이션', '립틴트', '틴트', '립밤', '립스틱', '립글로스', '립라이너', '섀도우', '쉐도우', '팔레트', '블러셔', '마스카라', '아이라이너', '브로우', '컨실러', '프라이머', '픽서', '파우더', '팩트', '하이라이터',
         '세럼', '앰플', '토너', '로션', '에멀전', '에센스', '수분크림', '크림', '마스크', '패드', '미스트', '올인원', '젤', '밤', '오일', '필링', '스크럽', '클렌저', '폼', '워시', '페이퍼', '리무버', '스프레이', '스틱', '티슈', '패치', '마스크시트', '팩', '아이크림', '넥크림']
FORM_RE = re.compile('|'.join(sorted(FORMS, key=len, reverse=True)))
FORM_MAP = {'크림미스트': '미스트', '쉐도우': '섀도우', '폼': '클렌징폼', '클렌저': '클렌징폼', '수분크림': '크림', '마스크시트': '마스크', '팩': '마스크', '톤업선크림': '선크림'}
def forms(n):
    fs = [FORM_MAP.get(m.group(), m.group()) for m in FORM_RE.finditer(n.replace(' ', ''))]
    return fs[-1] if fs else ''   # 마지막 제형 토큰 = 주 제형
DISCRIM = {'톤업', '포맨', '맨', '미니', '미니어처', '대용량', '리필', '키즈', '베이비', '프로', '플러스', '라이트', '딥', '오일프리', '젤', '쿨링', '워터프루프', '더마', '클리어', '수딩', '모공', '탄력', '흔적', '미백', '주름', '레드', '그린', '블루', '화이트', '블랙', '핑크', '골드', '바디', '헤어', '립', '아이', '넥', '핸드', '풋', '스칼프', '두피', '선', '톤업', '마일드', '센서티브', '인텐시브', '리치', '프레쉬', '매트', '글로우', '글로시', '벨벳', '샤인', '멜팅', '젤리', '워터', '밀크', '오일', '엠디', 'md', '패드'}
def score(a, b):
    shared = a['tk'] & b['tk']; sig = a['st'] & b['st']; d = dice(a['bg'], b['bg'])
    fa, fb = forms(a['nn']), forms(b['nn'])
    form_ok = fa == fb
    uni = a['st'] | b['st']; jac = len(sig) / len(uni) if uni else 0
    ca, cb = a['nn'].replace(' ', ''), b['nn'].replace(' ', '')
    diff = {t for t in (a['st'] ^ b['st']) & DISCRIM if (t in ca) != (t in cb)} | (nums(a['nn']) ^ nums(b['nn']))
    ok = bool(form_ok) and not diff and ((jac >= 0.5 and len(sig) >= 1) or d >= 0.7 or (len(sig) >= 2 and d >= 0.6))
    return ok, len(shared), len(sig), round(d, 3)
by_brand = collections.defaultdict(lambda: collections.defaultdict(list))
for i, p in enumerate(prods):
    if p['nb']: by_brand[p['nb']][p['source']].append(i)
cands = []   # 후보 쌍 (site pair, best match per product per target site)
SITES = ['oliveyoung', 'glowpick', 'hwahae', 'daisomall']
parent = list(range(len(prods)))
def find(x):
    while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
    return x
def union(a, b): parent[find(a)] = find(b)
# 사이트 내 변형(기획/더블/용량/중복키) = 정규화 이름이 같은 것끼리 먼저 묶음
cl = {}
for i, p in enumerate(prods):
    if True:   # 모든 사이트: 같은 사이트 안의 동일 정규화 이름(용량·기획 변형, 중복 키)은 한 클러스터
        k = (p['source'], p['nb'], p['nn'])
        if k in cl: union(i, cl[k])
        else: cl[k] = i
for nb, sites in by_brand.items():
    for sa, sb in itertools.combinations(SITES, 2):
        if sa not in sites or sb not in sites: continue
        def best_of(i, pool):
            best = None
            for j in pool:
                ok, sh, sg, d = score(prods[i], prods[j])
                if ok and (best is None or (sg, d) > (best[1], best[2])): best = (j, sg, d, sh)
            return best
        b_ab = {i: best_of(i, sites[sb]) for i in sites[sa]}
        b_ba = {j: best_of(j, sites[sa]) for j in sites[sb]}
        for i, best in b_ab.items():
            if not best: continue
            j, sg, d, sh = best
            back = b_ba[j]
            mutual = back is not None and find(back[0]) == find(i)
            cands.append(dict(src_a=sa, key_a=prods[i]['product_key'], name_a=prods[i]['name'], src_b=sb, key_b=prods[j]['product_key'], name_b=prods[j]['name'], brand=nb, shared_tok=sh, shared_sig=sg, dice=d, mutual=int(mutual), norm_a=prods[i]['nn'], norm_b=prods[j]['nn']))
            if mutual: union(i, j)
# ref 구성
groups = collections.defaultdict(list)
for i, p in enumerate(prods): groups[find(i)].append(i)
refs, members = [], []
for root, idx in groups.items():
    srcs = {prods[i]['source'] for i in idx}
    if len(srcs) < 2: continue
    anchor = next((i for i in idx if prods[i]['source'] == 'oliveyoung'), idx[0])
    rid = f"{prods[anchor]['source'][:2]}:{prods[anchor]['product_key']}"
    refs.append(dict(product_ref=rid, brand=prods[anchor]['brand'] or prods[anchor].get('brand_inferred', ''), name_norm=prods[anchor]['nn'], name=prods[anchor]['name'], n_sites=len(srcs), sites='+'.join(s for s in SITES if s in srcs), n_members=len(idx)))
    for i in idx: members.append(dict(product_ref=rid, source=prods[i]['source'], product_key=prods[i]['product_key'], name=prods[i]['name'], brand=prods[i]['brand'], volume=prods[i]['volume'], name_norm=prods[i]['nn']))
def w(fn, rs):
    with open(f'{D}/{fn}', 'w', newline='', encoding='utf-8') as f:
        wr = csv.DictWriter(f, fieldnames=list(rs[0].keys())); wr.writeheader(); wr.writerows(rs)
w('product_ref.csv', sorted(refs, key=lambda r: (-r['n_sites'], r['brand']))); w('product_ref_member.csv', members); w('product_ref_candidates.csv', cands)
print('cross-site refs', len(refs), collections.Counter(r['sites'] for r in refs))
print('pairs', len(cands), collections.Counter((c['src_a'], c['src_b']) for c in cands))
# 사이트별 커버리지
tot = collections.Counter(p['source'] for p in prods); cov = collections.Counter(m['source'] for m in members)
for s in SITES: print(s, cov[s], '/', tot[s])
