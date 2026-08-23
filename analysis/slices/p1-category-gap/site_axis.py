"""P1 Q2: 사이트 구조화 축(올영 review_topic, 다이소 review_answer) vs 자유텍스트 불만 aspect."""
import csv, collections
D = '/home/user1/github_prj/Main/architect/slice-p1-category-gap'
AXIS_MAP = {'자극도': '자극따가움', '향': '향냄새', '보습력': '건조', '수분감': '건조', '발림성': '제형발림', '세정력': '세정력거품', '거품': '세정력거품', '거품의 양': '세정력거품',
            '지속력': '지속력', '발색력': '색상발색', '머릿결': '모발손상', '두께감': '시트물성', '밀착력': '제형발림', '제형': '제형발림', '커버력': '커버력', '사용감': '제형발림',
            '기능': '효과없음', '편리함': '용기펌프', '광택': '색상발색', '펄감': '색상발색', '입자': '제형발림', '두피타입': '(피부타입)', '피부타입': '(피부타입)', '디자인': '용기펌프', '내구성': '용기펌프'}
NEG_ANSWER = {'자극이 느껴져요', '자극이 있어요', '다소 아쉬워요', '마음에 들지 않아요', '약간 건조해요', '매트해요', '거품이 적어요', '거품이 적어요 ', '예상보다 짧아요', '푸석여요', '생각보다 얇아요', '묽어요', '되직해요', '예상보다 약해요', '펄감이 강해요'}
denom = {r['product_key']: r for r in csv.DictReader(open(f'{D}/product_denominator.csv', encoding='utf-8'))}
topics = [r for r in csv.DictReader(open(f'{D}/site_topic_raw.csv', encoding='utf-8')) if r['product_key'] in denom]
metrics = list(csv.DictReader(open(f'{D}/metrics_by_category.csv', encoding='utf-8')))
# 카테고리별 사이트 축
cat_axes = collections.defaultdict(lambda: collections.defaultdict(list))   # cat -> axis -> [neg share per product]
for t in topics:
    cat = denom[t['product_key']]['category']
    cat_axes[cat][t['topic_group']].append((t['product_key'], t['topic_name'], int(t['share_pct'] or 0)))
out = []
for cat in sorted({m['category'] for m in metrics}):
    axes = cat_axes.get(cat, {})
    covered = {AXIS_MAP.get(a, a) for a in axes}
    site_neg = {}
    for a, lst in axes.items():
        negs = [s for pk, name, s in lst if name in NEG_ANSWER]
        site_neg[AXIS_MAP.get(a, a)] = round(sum(negs) / max(1, len({pk for pk, _, _ in lst})), 1)
    ms = sorted([m for m in metrics if m['category'] == cat], key=lambda m: -int(m['neg']))
    for m in ms[:8]:
        out.append(dict(category=cat, need_key=m['need_key'], scope=m['scope'], text_neg=m['neg'], text_pos=m['pos'], unresolved=m['unresolved'],
                        low_share=m['low_share'], population_share_pct=m['population_share_pct'],
                        site_axis_exists=m['need_key'] in covered, site_axis_names='|'.join(a for a in axes if AXIS_MAP.get(a, a) == m['need_key']),
                        site_neg_share_pct=site_neg.get(m['need_key'], ''), site_axes_all='|'.join(sorted(axes))))
with open(f'{D}/site_axis_vs_text.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
for cat in sorted({o['category'] for o in out}):
    rows = [o for o in out if o['category'] == cat]
    print(f"\n== {cat}  사이트 축: {rows[0]['site_axes_all'] or '(없음)'}")
    for o in rows: print(f"  {o['need_key']:8s} 불만{o['text_neg']:>4s} 미해결{o['unresolved']:>5s} 모집단{str(o['population_share_pct']):>6s}%  사이트축={'O' if o['site_axis_exists'] else '-'} {o['site_axis_names']:10s} 사이트부정%={o['site_neg_share_pct']}")
# 다이소
ans = list(csv.DictReader(open(f'{D}/site_answer_raw.csv', encoding='utf-8')))
print('\n다이소 설문 축 분포:'); c = collections.Counter((a['question_name'], a['answer']) for a in ans)
for (q, a), n in sorted(c.items()): print(f'  {q:6s} {a:12s} {n}')
