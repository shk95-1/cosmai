"""Q1 보강: lag별 |Δrank| (1,2,3,6,12,24,48h), Δ의 1차 자기상관, 일 내/일 간 분산 분해, 시간대(KST)별 churn. 출력: rank_grain.csv, churn_by_hour.csv"""
import sqlite3, csv, statistics, collections, os
from datetime import datetime
D = os.path.dirname(os.path.abspath(__file__)); db = sqlite3.connect(f'{D}/slice.db')
rows = db.execute("select source,board,category_key,captured_at,product_key,rank from rank_snapshot").fetchall()
ser = collections.defaultdict(dict)   # (unit, pk) -> {hour_index: rank}
def hi(ts): return int(datetime.fromisoformat(ts.replace('+00', '+00:00')).timestamp() // 3600)
for s, b, c, ts, pk, r in rows: ser[((s, b, c), pk)][hi(ts)] = int(r)
by_unit = collections.defaultdict(list)
for (u, pk), d in ser.items(): by_unit[u].append(d)
out = []
for u, dl in sorted(by_unit.items()):
    if u[0] == 'glowpick' and u[2] not in ('41', '3', '4'): continue
    rec = dict(source=u[0], board=u[1], category_key=u[2])
    for lag in (1, 2, 3, 6, 12, 24, 48):
        ds = [abs(d[h + lag] - d[h]) for d in dl for h in d if h + lag in d]
        rec[f'absdelta_lag{lag}h'] = round(statistics.mean(ds), 2) if ds else ''; rec[f'n_lag{lag}h'] = len(ds)
    # Δ 자기상관 (1h): corr(Δ_t, Δ_{t+1}) – 음수면 평균회귀(노이즈)
    xs, ys = [], []
    for d in dl:
        for h in d:
            if h + 1 in d and h + 2 in d: xs.append(d[h + 1] - d[h]); ys.append(d[h + 2] - d[h + 1])
    if len(xs) > 10 and statistics.pstdev(xs) > 0 and statistics.pstdev(ys) > 0:
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs); rec['delta_autocorr_1h'] = round(cov / (statistics.pstdev(xs) * statistics.pstdev(ys)), 3)
    else: rec['delta_autocorr_1h'] = ''
    # 분산 분해: 제품별 (전체 변동) = 일 간 + 일 내. 3일 이상·하루 6 스냅샷 이상 있는 제품만
    within, between = [], []
    for d in dl:
        days = collections.defaultdict(list)
        for h, r in d.items(): days[(h + 9) // 24].append(r)    # KST 일
        days = {k: v for k, v in days.items() if len(v) >= 6}
        if len(days) < 3: continue
        dm = {k: statistics.mean(v) for k, v in days.items()}
        within.append(statistics.mean(statistics.pvariance(v) for v in days.values()))
        between.append(statistics.pvariance(list(dm.values())))
    if within:
        w, b = statistics.mean(within), statistics.mean(between)
        rec['var_within_day'] = round(w, 1); rec['var_between_day'] = round(b, 1); rec['within_share'] = round(w / (w + b), 3) if w + b else ''; rec['n_products_vardecomp'] = len(within)
    out.append(rec)
with open(f'{D}/rank_grain.csv', 'w', newline='') as f:
    wr = csv.DictWriter(f, fieldnames=list(out[0].keys())); wr.writeheader(); wr.writerows(out)
for r in out: print({k: v for k, v in r.items() if not k.startswith('n_')})
# 시간대별 churn (oliveyoung 전체 보드 평균, daisomall sale_rising)
pairs = list(csv.DictReader(open(f'{D}/rank_churn_pairs.csv')))
hb = collections.defaultdict(list)
for p in pairs:
    if p['gap_h'] != '1.0': continue
    key = p['source'] if p['source'] != 'daisomall' else f"daisomall/{p['board']}"
    hb[(key, int(p['hour_kst']))].append(float(p['topk_churn']))
hrows = [dict(site=k[0], hour_kst=k[1], n=len(v), topk_churn_mean=round(statistics.mean(v), 3)) for k, v in sorted(hb.items())]
with open(f'{D}/churn_by_hour.csv', 'w', newline='') as f:
    wr = csv.DictWriter(f, fieldnames=list(hrows[0].keys())); wr.writeheader(); wr.writerows(hrows)
for h in hrows:
    if h['site'] in ('oliveyoung', 'glowpick', 'daisomall/sale_daily'): print(h)
