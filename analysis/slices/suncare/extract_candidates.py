"""선크림 슬라이스 1차: 표지 정규식으로 불만/바람 후보 문장 추출 (LLM 없음)."""
import csv, re, sys, collections
D = sys.argv[1]
SUN = re.compile(r'선크림|썬크림|선블록|선스크린|선쿠션|선스틱|자외선 ?차단|선비비|선제품|선케어')
WISH = re.compile(r'면 좋겠|았으면|었으면|나왔으면|있었으면|출시[됐되해]|해주세요|해줬으면|만들어 ?줘|있으면 좋|없나요|없을까|왜 없')
COMPLAINT = re.compile(r'아쉬|단점|별로|불편|근데|다만|빼고는|하지만|그런데|백탁|밀림|밀려|끈적|눈시림|눈이 시|눈 시|따가|따끔|뒤집|트러블|번들|번질|기름지|유분|건조|당김|당겨|뭉침|뭉쳐|들뜨|들떠|재구매 ?안|실망|별루|무거|답답|냄새|향이 ?별|용량|비싸|가격|불안|기대 이하|그닥|쏘|화끈|묻어나|묻어남')
SPLIT = re.compile(r'(?<=[.!?~ㅠㅜ])\s+|(?<=요)\s+(?=[가-힣])|(?<=다)\s+(?=[가-힣])|(?<=[ㅎㅋ]{2})\s+')

def sentences(text):
    parts = [p.strip() for p in SPLIT.split(text) if p and len(p.strip()) > 4]
    return parts or [text.strip()]

def tag(s):
    w = WISH.search(s); c = COMPLAINT.search(s)
    if w: return 'wish', w.group(0)
    if c: return 'complaint', c.group(0)
    return None, None

out = []
# A. reviews
for r in csv.DictReader(open(f'{D}/_reviews_raw.csv', encoding='utf-8')):
    rating = float(r['rating']) if r['rating'] else None
    for s in sentences(r['body']):
        kind, m = tag(s)
        if kind is None and not (rating is not None and rating <= 3):
            continue
        out.append(dict(src='review', site=r['source'], ref=f"{r['product_key']}/{r['review_key']}",
                        subject=r['product_name'], observed_at=r['written_at'], weight=rating,
                        kind=kind or 'low_rating', marker=m or f'rating={rating}', sentence=s))
# B. transcripts: window around sunscreen mention, then markers
for r in csv.DictReader(open(f'{D}/_transcripts_raw.csv', encoding='utf-8')):
    txt = r['full_text']
    for m in SUN.finditer(txt):
        win = txt[max(0, m.start()-90): m.end()+90]
        kind, mk = tag(win)
        if kind:
            out.append(dict(src='yt_transcript', site=r['channel'], ref=r['video_id'], subject=r['title'],
                            observed_at=r['published_at'], weight=r['view_count'], kind=kind, marker=mk, sentence=win))
# C. comments
for r in csv.DictReader(open(f'{D}/_comments_raw.csv', encoding='utf-8')):
    for s in sentences(r['text']):
        if not SUN.search(s): continue
        kind, mk = tag(s)
        if kind:
            out.append(dict(src='yt_comment', site='youtube', ref=f"{r['video_id']}/{r['comment_id']}", subject='',
                            observed_at=r['published_at'], weight=r['like_count'], kind=kind, marker=mk, sentence=s))

# dedupe identical sentences within src
seen=set(); ded=[]
for o in out:
    k=(o['src'],o['ref'],o['sentence'])
    if k in seen: continue
    seen.add(k); ded.append(o)
with open(f'{D}/candidates.csv','w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f, fieldnames=list(ded[0].keys())); w.writeheader(); w.writerows(ded)
c=collections.Counter((o['src'],o['kind']) for o in ded)
print('total',len(ded)); [print(f'{k[0]:14s} {k[1]:11s} {v}') for k,v in sorted(c.items())]
print('--- top markers'); print(collections.Counter(o['marker'] for o in ded if o['kind']!='low_rating').most_common(25))
print('--- reviews by month (oliveyoung)'); 
print(sorted(collections.Counter(o['observed_at'][:7] for o in ded if o['src']=='review' and o['site']=='oliveyoung').items()))
