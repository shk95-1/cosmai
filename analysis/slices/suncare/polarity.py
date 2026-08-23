"""극성 판정 규칙 v2: 문장 → (aspect, polarity[불만|만족|중립], reason). LLM 없음.
v1 → v2 변경: rating 폴백 제거, 부정어('않/심하지 않/크게 않')를 불만어에서 분리, 타제품·취향·피부타입 맥락 제외,
'재구매 안함/추천 안함' 부정 만족어 처리, 중립 aspect(발림/색상/가격/지속)는 불만어 동반 시에만 불만, 어휘 보강."""
import re, csv, sys, collections

ASPECTS = {
    '백탁':      r'백탁|하얗게 ?[뜨떠]|허옇|회색빛|하얘[지져]|다크닝|안색이 ?안',
    '끈적유분':  r'끈적|유분|번들|번질|기름[지기이]|개기름|기름 ?[돌올]|기름져|미끌|찐득|꾸덕|잔여감|찝찝|겉돌',
    '답답무거움': r'답답|무거|무겁|막 ?낀|막이 ?[느껴생]|뭐 ?바른 ?것 ?처럼|바른 ?것처럼|두껍게',
    '건조':      r'건조|당김|당겨|땡겨|속건조|푸석|각질 ?부각|각질이 ?[일올]',
    '눈시림':    r'눈 ?시[림리러립려]|눈이 ?(좀 |많이 |너무 )?[시따맵]|눈 ?따[가갑]|눈 ?[맵매]|눈에 들어가',
    '자극따가움': r'따가|따갑|따끔|아프|화끈|쓰라|자극적|자극이|자극을|자극 ?심|열감|홍조|빨개|빨갰|빨갛|간지|가렵|화[한함]',
    '밀림':      r'밀림|밀려|밀[리릴린]|때처럼|때 ?같|때 ?밀|뭉침|뭉쳐|뭉[치침]|들뜨|들떠|갈라|각질처럼|덩어리|떠보|뜨[고네]',
    '트러블':    r'트러블|뒤집|뾰[루류]지|뭐가 ?나|좁쌀|여드름|올라[오왔]|피부가 ?안 ?좋아|피부염',
    '향':        r'향이|향은|향도|냄새|향 ?때문|향 ?별로|향 ?이상',
    '용량가격':  r'용량|가격|비싸|비쌈|가성비|양이 ?[적작]|할인|사은품',
    '발림텍스처': r'발림|발리|제형|텍스처|흡수|묽|뻑뻑|퍽퍽|되직|잘 ?펴|안 ?펴|밀착',
    '색상어두움': r'어두[운움워]|촌스|흙빛|낯빛|회끼|노랗|시멘트',
    '톤업색상':  r'톤업|톤 ?업|색상|컬러|호수|밝[아게]|핑크끼|피부톤',
    '지속력워터': r'지속|워터프루프|땀|물놀이|무너|지워|묻어|번짐|번져|쌩얼되|찍힘',
    '기타불만':  r'실망|최악|별로|당근행|처박|돈 ?낭비|절대 ?불가|불가$|좋다고 ?느끼진 ?못|그렇게 ?[가-힣]+지는 ?않|그닥|별루|쓰레기|돈 ?아깝|환불|버렸|후회|짜증|기대 ?이하|흘러있|새서|터져',
}
NEUTRAL_ASPECTS = {'발림텍스처', '톤업색상', '용량가격', '지속력워터'}
ASPECT_RE = {k: re.compile(v) for k, v in ASPECTS.items()}

POS = re.compile(r'좋[아은다네요]|좋고|좋습|괜찮|나쁘지 ?않|만족|추천|최고|굿|짱|대박|편[해하했]|순[해하]|촉촉|보송|산뜻|가볍|가벼|부드럽|완벽|인생|재구매|또 ?살|잘 ?맞|마음에 ?[들듭]|맘에 ?[들듭]|강추|사랑|딱이|무난')
POS_NEGATED = re.compile(r'(재구매|구입|구매|추천)[은는를도]?\s?(안|않|절대|다신|다시는)|다신 ?(구입|구매|사지)|절대 ?사지|손이 ?안 ?가|손에 ?많이 ?가지는 ?않|그냥 ?그래|나을 ?듯|미지수|기대했는데|못 ?쓰|안 ?맞|안 ?좋|비추|다른 ?제품 ?쓰')
WISH = re.compile(r'좋겠|좋았으면|었으면|았으면')
NEG_STRONG = re.compile(r'너무 ?(심|많|세|강|답답|무거|건조|끈적|따가|밀|두껍)|(?<!심하지 )심[하해했함]|엄청 ?(심|따|밀|건조|끈적|어두)|진짜 ?(심|많|따|밀|건조|끈적)|싫|별로|최악|실망|못 ?쓰|안 ?맞|안 ?좋|그닥|아쉬|아쉽|단점|불편|후회|환불|버렸|돈 ?아깝|짜증|기대 ?이하|쓰레기|때처럼|개[따뻑별]|레전드|충격|중단|신중|테스트 ?해보|피하는게|비추|어두[운움워]|흘러[내있]|힘들|못 ?쓸|안 ?먹|잘 ?모르겠|떨어지는|쓰면 ?안|알될|안될')
NEG_AFTER = re.compile(r'^.{0,14}?(없|않|안(?= ?[가-힣])|적[고어은다]|덜|제로|zero|1도|하나도|전혀|거의|심하지|크게 ?(느껴지지|않)|X|x)', re.I)
NEG_BEFORE = re.compile(r'(없|않|안 |덜|전혀|하나도|1도|심하지)\s?.{0,6}$')
# aspect가 '이 제품'이 아닌 것에 붙는 맥락
CONTEXT_BEFORE = re.compile(r'(다른 ?(선크림|제품|거)|유기자차[는은]?|무기자차[는은]?|사용하던|기존|전에 ?쓰던|예전|평소|특유의|선크림 ?특유|선크림[은는]? ?(끈적|답답|백탁)|날씨|가을|겨울|피부 ?타입|피부[가는]? ?(좀 )?(건조|예민|민감)|프라이머|파데|쿠션만|후기에|리뷰에|리뷰가|후기가)\s?[가-힣]{0,6}$')
SKIN_CONTEXT = re.compile(r'(피부 ?타입|피부입니다|피부예요|피부에요|편이에요|편입니다|피부 ?\.\.|싶으신 ?분|이신 ?분|하신 ?분|분들은|분들에게|분이라면|분들이면|피부라면)')
WARNING = re.compile(r'안 ?쓰|신중|비추|테스트|피하|고민|주의|조심')
CONTRAST_THIS = re.compile(r'(는데|지만|인데)\s?(얘는|이건|이거는|이 ?제품은|요건|이게|얘가)|싫어하는데|싫어서|싫은데')

def classify(sentence, rating=None):
    s = sentence
    found = []
    for key, rx in ASPECT_RE.items():
        for m in rx.finditer(s):
            after = s[m.end():m.end()+16]; before = s[max(0, m.start()-22):m.start()]
            if CONTEXT_BEFORE.search(before):
                found.append((key, m.group(0), None, m.start())); continue   # 맥락(타제품/취향)
            negated = bool(NEG_AFTER.search(after)) or bool(NEG_BEFORE.search(before))
            found.append((key, m.group(0), negated, m.start()))
    has_wish = bool(WISH.search(s))
    has_pos = bool(POS.search(s)) and not POS_NEGATED.search(s) and not (has_wish and not re.search(r'없어서|없고|않아서|않고', s))
    has_neg = bool(NEG_STRONG.search(s)) or bool(POS_NEGATED.search(s))
    ctx = [f for f in found if f[2] is None]
    real = [f for f in found if f[2] is not None]
    # 피부타입/타인 대상 문장
    if SKIN_CONTEXT.search(s) and not CONTRAST_THIS.search(s):
        if WARNING.search(s) or POS_NEGATED.search(s): return (real or ctx or [(None,)])[0][0], '불만', 'warning-to-others'
        if has_pos and not has_neg and real: return real[0][0], '만족', 'skin-ctx+pos'
        return (real or ctx or [(None,)])[0][0], '중립', 'skin-context'
    if not real:
        if has_neg and not has_pos: return (ctx[0][0] if ctx else None), '불만', 'neg-only'
        if has_pos and not has_neg: return (ctx[0][0] if ctx else None), '만족', 'pos-only'
        return (ctx[0][0] if ctx else None), '중립', 'no-aspect' if not ctx else 'context-only'
    neg_aspects = [f for f in real if f[2]]
    raw = [f for f in real if not f[2]]
    raw_complaint = [f for f in raw if f[0] not in NEUTRAL_ASPECTS]
    raw_neutral = [f for f in raw if f[0] in NEUTRAL_ASPECTS]
    if CONTRAST_THIS.search(s):   # "X 싫어하는데 얘는 …": 뒤 절이 결론
        tail = s[CONTRAST_THIS.search(s).end():]
        tail_found = [f for f in real if f[3] >= CONTRAST_THIS.search(s).start()]
        tail_raw = [f for f in tail_found if not f[2] and f[0] not in NEUTRAL_ASPECTS]
        if tail_raw and NEG_STRONG.search(tail): return tail_raw[0][0], '불만', 'contrast-tail-raw'
        if POS.search(tail) or any(f[2] for f in tail_found): return (tail_found or real)[0][0], '만족', 'contrast-tail-pos'
    if raw_complaint:
        key = raw_complaint[0][0]
        if has_pos and not has_neg and neg_aspects and len(neg_aspects) >= len(raw_complaint):
            return key, '만족', 'mostly-negated+pos'
        return key, '불만', 'aspect-raw'
    if raw_neutral and not neg_aspects:
        key = raw_neutral[0][0]
        if has_neg and not has_pos: return key, '불만', 'neutral-aspect+neg'
        if has_pos: return key, '만족', 'neutral-aspect+pos'
        return key, '중립', 'neutral-aspect-only'
    # 남은 경우: 부정된 aspect(=만족)
    key = neg_aspects[0][0]
    if has_neg and not has_pos and not NEG_AFTER.search(s[neg_aspects[0][3]:]): return key, '불만', 'aspect-negated+neg'
    return key, '만족', 'aspect-negated'

if __name__ == '__main__':
    D = sys.argv[1]
    rows = list(csv.DictReader(open(f'{D}/candidates.csv', encoding='utf-8')))
    for r in rows:
        rating = float(r['weight']) if r['src'] == 'review' and r['weight'] else None
        a, p, why = classify(r['sentence'], rating)
        r['aspect'] = a or ''; r['polarity'] = p; r['rule'] = why
    with open(f'{D}/candidates_polarity.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    c = collections.Counter((r['src'], r['polarity']) for r in rows)
    for k, v in sorted(c.items()): print(f'{k[0]:14s} {k[1]:4s} {v}')
