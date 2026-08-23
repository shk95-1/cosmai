"""P1 Q3: 글로우픽(넓고 얕음) vs 올리브영(좁고 깊음) 카테고리별 aspect 불만 순위 비교. + 다이소 설문 vs 텍스트."""
import csv, collections
D = '/home/user1/github_prj/Main/architect/slice-p1-category-gap'
nm = list(csv.DictReader(open(f'{D}/need_mention.csv', encoding='utf-8')))
PAIR = {'선크림': '선블록', '페이셜클렌저': '클렌징폼', '에센스/세럼': '에센스', '시트마스크': '시트팩', '크림': '크림', '샴푸': '샴푸', '헤어트리트먼트': '헤어트리트먼트', '립틴트/라커': '립틴트', '파운데이션': '쿠션', '쿠션': '쿠션', '패드': '패드', '스킨/토너': '스킨/토너'}
def shares(rows):
    neg = collections.Counter(m['need_key'] for m in rows if m['polarity'] == '불만'); pos = collections.Counter(m['need_key'] for m in rows if m['polarity'] == '만족')
    tot = sum(neg.values())
    return {k: (neg[k], pos[k], round(neg[k] / tot, 2) if tot else 0) for k in set(neg) | set(pos)}, tot
out = []
print(f"{'glowpick cat':12s} → {'oliveyoung cat':10s} | gp 리뷰/불만 | oy 불만 | 상위5 비교 (aspect: gp점유율 / oy점유율)")
for gc, oc in PAIR.items():
    g = [m for m in nm if m['site'] == 'glowpick' and m['category'] == gc]; o = [m for m in nm if m['site'] == 'oliveyoung' and m['category'] == oc]
    if not g or not o: continue
    gs, gt = shares(g); os_, ot = shares(o)
    gtop = [k for k, _ in sorted(gs.items(), key=lambda x: -x[1][0])[:5]]; otop = [k for k, _ in sorted(os_.items(), key=lambda x: -x[1][0])[:5]]
    overlap = len(set(gtop) & set(otop))
    grev = len({m['review_key'] for m in g}); oprod = len({m['product_key'] for m in o}); gprod = len({m['product_key'] for m in g})
    for k in sorted(set(gtop) | set(otop), key=lambda k: -(os_.get(k, (0, 0, 0))[2] + gs.get(k, (0, 0, 0))[2])):
        out.append(dict(glowpick_category=gc, oliveyoung_category=oc, need_key=k, gp_neg=gs.get(k, (0, 0, 0))[0], gp_pos=gs.get(k, (0, 0, 0))[1], gp_share=gs.get(k, (0, 0, 0))[2],
                        oy_neg=os_.get(k, (0, 0, 0))[0], oy_pos=os_.get(k, (0, 0, 0))[1], oy_share=os_.get(k, (0, 0, 0))[2], gp_products=gprod, gp_reviews=grev, oy_products=oprod, top5_overlap=overlap))
    print(f"{gc:12s} → {oc:10s} | gp {gprod}제품 {grev}리뷰 불만{gt:3d} | oy {oprod}제품 불만{ot:4d} | top5 겹침 {overlap}/5 | " + ', '.join(f"{k}:{gs.get(k,(0,0,0))[2]:.2f}/{os_.get(k,(0,0,0))[2]:.2f}" for k in otop))
with open(f'{D}/depth_vs_breadth.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
# 다이소: 설문 부정 응답 vs 텍스트 불만
ans = list(csv.DictReader(open(f'{D}/site_answer_raw.csv', encoding='utf-8')))
NEG = {'자극이 있어요': '자극따가움', '약간 건조해요': '건조', '마음에 들지 않아요': '향냄새', '거품이 적어요 ': '세정력거품', '생각보다 얇아요': '시트물성'}
sv = collections.Counter(NEG[a['answer']] for a in ans if a['answer'] in NEG and a['question_name'] != '기능')
dm = [m for m in nm if m['site'] == 'daisomall']
tx = collections.Counter(m['need_key'] for m in dm if m['polarity'] == '불만')
n_reviews_answered = len({a['review_key'] for a in ans})
print(f"\n다이소: 설문 응답 리뷰 {n_reviews_answered}, 텍스트 불만 리뷰-aspect {sum(tx.values())}")
print('  설문 부정:', dict(sv)); print('  텍스트 불만 top:', tx.most_common(10))
