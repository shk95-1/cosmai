#!/usr/bin/env python3
"""후향 검증. (기획안 08.25 — "과거 구간을 이용한 후향 검증 사례 2건 이상 확보")

무엇을 하나. 과거 분기 C 까지만 알고 있었던 것처럼 지표를 다시 계산해 **직전
분기 T 를 판정**하고, C 이후 4분기에 실제로 그 방향이 유지됐는지 본다. 판정이
"지나고 보니 그랬다"가 아니라 "그때 알 수 있었다"인지 확인하는 유일한 방법이다.

왜 T 가 아니라 C 로 자르나. `judge.py` 는 마지막 분기를 `미확정(진행 중)` 으로
두기 때문이다(분기가 아직 안 끝났으니 맞는 규칙이다). 그래서 T 를 판정하려면
C = T + 1 까지 데이터가 있어야 한다. 실제 운영도 그렇게 돌아간다.

미래 누출을 막는 게 이 스크립트의 핵심이다. `persistence` 의 baseline 이 **전체
기간 중앙값**이라, 자르지 않고 과거 분기를 판정하면 아직 오지 않은 분기를 보고
중앙값을 정한 셈이 된다. 그래서 `trend.CUTOFF_QUARTER` 로 T 이후를 없는 것처럼
지표를 다시 계산한다. velocity 는 전년 동분기만 쓰므로 원래 누출이 없다.

판정별 적중 기준. 방향이 있는 유형만 검증한다. 구간을 1년(4분기)으로 둔 것은
계절성 때문이다 — 직전·이후 둘 다 네 분기를 다 담으므로 여름 효과가 상쇄된다.

    급상승 · 신규 등장   이후 4분기 평균 구성비 > 직전 4분기 평균
    사라짐              이후 4분기 평균 구성비 < 직전 4분기 평균
    단기 피크           이후 4분기 평균 구성비 < T 분기 구성비 (피크가 안 유지됨)

**기준을 두 개 낸다.** 기준 A 의 직전 구간에는 급상승한 분기 T 자체가 들어 있다.
그러면 "T 보다 더 올라야 적중"이 되어 평균 회귀만으로 실패가 나온다. 기준 B 는
T 를 뺀 직전 4분기와 비교한다 — 즉 "**올라간 수준이 유지됐는가**"를 묻는다.
두 질문이 다르고, 둘 중 하나만 내면 결과를 고른 것이 된다.

`지속 인기` · `채널 확산` 은 방향 예측이 아니라 상태 서술이므로 뺀다. 넣으면
적중률이 부풀려진다.

**기저율을 같이 낸다.** 판정과 무관하게 전체 셀 중 몇 %가 올랐는지를 함께
계산한다. 급상승 적중률이 기저율보다 높지 않으면 그 판정은 정보가 없다.
적중률만 내는 후향 검증은 검증이 아니라 홍보다.

사용법:
    python backtest.py data/panel/run_A data/panel/run_B
"""
from __future__ import annotations

import argparse
import csv
import io
import statistics
from pathlib import Path

import judge
import trend

csv.field_size_limit(10 ** 8)

HORIZON = 4          # 검증 구간 길이(분기)
LOOKBACK = 4         # 비교 기준 구간 길이(분기)
UP_TYPES = {"급상승", "신규 등장"}
DOWN_TYPES = {"사라짐"}
PEAK_TYPES = {"단기 피크"}
FIELDS = ["cutoff", "source", "topic_id", "trend_type", "before_pp", "before_excl_pp",
          "after_pp", "at_cutoff_pp", "expected", "actual", "hit", "hit_level"]


def all_quarters(rows: list[dict]) -> list[str]:
    return sorted({r["quarter"] for r in rows})


def next_quarters(quarters: list[str], cutoff: str, n: int) -> list[str]:
    after = [q for q in quarters if q > cutoff]
    return after[:n]


def prior_quarters(quarters: list[str], cutoff: str, n: int) -> list[str]:
    upto = [q for q in quarters if q <= cutoff]
    return upto[-n:]


def as_of(run_dirs: list[Path], panel: dict[str, str], cutoff: str | None) -> list[dict]:
    """cutoff 까지만 아는 상태로 지표·판정을 계산한다."""
    trend.CUTOFF_QUARTER = cutoff
    try:
        rows = []
        for source in ("video", "comment"):
            rows.extend(trend.build_rows(run_dirs, panel, source))
    finally:
        trend.CUTOFF_QUARTER = None
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    judged, _ = judge.judge(list(csv.DictReader(buf)))
    return judged


def mean_composition(full: dict, source: str, topic: str, quarters: list[str]) -> float:
    values = [full[(source, topic, q)] for q in quarters if (source, topic, q) in full]
    return 100 * statistics.fmean(values) if values else 0.0


def run(run_dirs: list[Path], panel_csv: Path, out: Path) -> int:
    panel = trend.load_panel(panel_csv)

    # 실제로 일어난 일. 미래 값은 여기서만 읽는다
    truth_rows = as_of(run_dirs, panel, None)
    full = {(r["source"], r["topic_id"], r["quarter"]): float(r["composition"] or 0)
            for r in truth_rows}
    quarters = all_quarters(truth_rows)

    # C 는 잘라 낸 시점, T = C 직전 분기가 판정 대상이다. C 이후 4분기로 검증한다
    cutoffs = [(quarters[i], quarters[i - 1]) for i in range(LOOKBACK, len(quarters))
               if len(next_quarters(quarters, quarters[i], HORIZON)) == HORIZON]
    print(f"전체 {len(quarters)}분기 ({quarters[0]}~{quarters[-1]}) 중 "
          f"검증 가능한 시점 {len(cutoffs)}개: "
          f"{', '.join(f'{t}(자름 {c})' for c, t in cutoffs)}")
    print()

    rows: list[dict] = []
    for cutoff, target in cutoffs:
        judged = as_of(run_dirs, panel, cutoff)
        before_qs = prior_quarters(quarters, target, LOOKBACK)
        before_excl_qs = [q for q in prior_quarters(quarters, target, LOOKBACK + 1)
                          if q != target]
        after_qs = next_quarters(quarters, cutoff, HORIZON)
        for r in judged:
            if r["quarter"] != target or r["judged"] != "true":
                continue
            kind = r["trend_type"]
            if kind not in UP_TYPES | DOWN_TYPES | PEAK_TYPES:
                continue
            source, topic = r["source"], r["topic_id"]
            before = mean_composition(full, source, topic, before_qs)
            # 급상승 판정 분기를 뺀 직전 구간. 두 기준을 왜 다 내는지는 §demo 위 주석
            before_excl = mean_composition(full, source, topic, before_excl_qs)
            after = mean_composition(full, source, topic, after_qs)
            at = 100 * float(r["composition"] or 0)
            if kind in UP_TYPES:
                expected, hit, level = "상승 유지", after > before, after > before_excl
            elif kind in DOWN_TYPES:
                expected, hit, level = "하락 유지", after < before, after < before_excl
            else:
                expected, hit, level = "피크 소멸", after < at, after < at
            rows.append({
                "cutoff": target, "source": source, "topic_id": topic,
                "trend_type": kind,
                "before_pp": round(before, 2), "before_excl_pp": round(before_excl, 2),
                "after_pp": round(after, 2),
                "at_cutoff_pp": round(at, 2), "expected": expected,
                "actual": "상승" if after > before else "하락",
                "hit": "true" if hit else "false",
                "hit_level": "true" if level else "false",
            })

    # 기저율. 판정과 무관하게 이후 4분기 평균이 오른 셀의 비율
    base_hits = base_level_hits = base_total = 0
    for cutoff, target in cutoffs:
        before_qs = prior_quarters(quarters, target, LOOKBACK)
        before_excl_qs = [q for q in prior_quarters(quarters, target, LOOKBACK + 1)
                          if q != target]
        after_qs = next_quarters(quarters, cutoff, HORIZON)
        for source in ("youtube_video", "youtube_comment"):
            for topic in trend.TREND_TOPICS:
                before = mean_composition(full, source, topic, before_qs)
                before_excl = mean_composition(full, source, topic, before_excl_qs)
                after = mean_composition(full, source, topic, after_qs)
                if before == 0 and after == 0:
                    continue
                base_total += 1
                base_hits += after > before
                base_level_hits += after > before_excl
    base_rate = 100 * base_hits / base_total if base_total else 0.0
    base_level_rate = 100 * base_level_hits / base_total if base_total else 0.0

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    if not rows:
        print("검증 사례가 없다. 판정된 방향성 셀이 과거 시점에 없다.")
        print(f"{out} 저장")
        return 1

    print(f"{'시점':<8}{'소스':<16}{'주제':<14}{'판정':<8}"
          f"{'직전4Q':>8}{'이후4Q':>8}  결과")
    for r in rows:
        print(f"{r['cutoff']:<8}{r['source']:<16}{r['topic_id']:<14}{r['trend_type']:<8}"
              f"{r['before_pp']:>8.2f}{r['after_pp']:>8.2f}  "
              f"{'적중' if r['hit'] == 'true' else '실패'} ({r['expected']})")
    hits = sum(1 for r in rows if r["hit"] == "true")
    level_hits = sum(1 for r in rows if r["hit_level"] == "true")
    print()
    print(f"방향성 판정 {len(rows)}건")
    print(f"  기준 A(계속 상승) 적중 {hits}건 {100 * hits / len(rows):.0f}% "
          f"vs 기저율 {base_rate:.0f}%")
    print(f"  기준 B(수준 유지) 적중 {level_hits}건 {100 * level_hits / len(rows):.0f}% "
          f"vs 기저율 {base_level_rate:.0f}%")
    ups = [r for r in rows if r["trend_type"] in UP_TYPES]
    if ups:
        a = 100 * sum(1 for r in ups if r["hit"] == "true") / len(ups)
        b = 100 * sum(1 for r in ups if r["hit_level"] == "true") / len(ups)
        print(f"  상승 계열 {len(ups)}건만 — A {a:.0f}% (기저 {base_rate:.0f}%) · "
              f"B {b:.0f}% (기저 {base_level_rate:.0f}%)")
        print(f"  {'기저율을 넘는다' if b > base_level_rate else '기저율을 넘지 못했다'}"
              f" — 표본 {len(ups)}건이라 이 숫자로 성능을 주장하지 않는다")
    print()
    print(f"{out} 저장")
    return 0 if len(rows) >= 2 else 1


def demo() -> None:
    qs = ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2"]
    assert next_quarters(qs, "2024Q2", 2) == ["2024Q3", "2024Q4"]
    assert next_quarters(qs, "2025Q2", 2) == []
    assert prior_quarters(qs, "2024Q2", 4) == ["2024Q1", "2024Q2"]
    assert prior_quarters(qs, "2025Q2", 2) == ["2025Q1", "2025Q2"]
    # 자르는 시점의 값은 직전 구간에 들어간다(포함), 이후 구간에는 안 들어간다
    assert "2024Q2" in prior_quarters(qs, "2024Q2", 4)
    assert "2024Q2" not in next_quarters(qs, "2024Q2", 4)
    full = {("s", "t", "2024Q1"): 0.1, ("s", "t", "2024Q2"): 0.3}
    assert abs(mean_composition(full, "s", "t", ["2024Q1", "2024Q2"]) - 20.0) < 1e-9
    assert mean_composition(full, "s", "t", ["2030Q1"]) == 0.0
    # 훅이 있어야 미래 누출을 막을 수 있다
    assert hasattr(trend, "CUTOFF_QUARTER")
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dirs", nargs="*", type=Path)
    p.add_argument("--panel", type=Path, default=Path("seeds/channels_v1.csv"))
    p.add_argument("--out", type=Path, default=Path("reports/backtest.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    if not a.run_dirs:
        p.error("run_dirs 를 하나 이상 지정하거나 --demo 를 쓴다")
    return run(a.run_dirs, a.panel, a.out)


if __name__ == "__main__":
    raise SystemExit(main())
