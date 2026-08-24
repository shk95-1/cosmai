#!/usr/bin/env python3
"""패널 구성이 판정 결론을 바꾸는지 측정한다. (A1 — panel_role 재분류 검증)

배경. 채널 43개 중 23개는 채널명 규칙(`role_basis = name_rule`)으로 product/expert 를
잠정 분류했다. TEAM_DECISIONS_v0.2 §4.1 은 수집이 끝나면 실제 업로드 구성으로
재분류하라고 적어뒀다.

재분류를 시도한 결과, **텍스트 지표로는 두 집단이 구분되지 않았다.** 팀이 직접
분류한 20채널을 기준으로 성분·스펙 주제 비중을 재보면 expert 중앙값 17.7%,
product 15.3% 로 범위가 거의 겹친다(product 인 디렉터파이가 39.7% 로 최고값이다).
선크림 언급률도 뷰티 채널 중 선크림을 덜 다루는 곳과 전문가 채널이 같은 구간에 있다.

그래서 개별 채널을 옮기는 대신, **패널 선택이 결론을 바꾸는지**를 측정한다.
기획안 §4 의 "결론이 필터 조건에 따라 크게 달라지면 필터 민감 신호로 표시한다"가
요구하는 검사가 이것이다. 바뀌지 않으면 현행 분류를 그대로 확정할 수 있다.

사용법:
    python panel_sensitivity.py data/panel/run_A data/panel/run_B
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

import trend

RECENT = ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]
PRIOR = ["2024Q3", "2024Q4", "2025Q1", "2025Q2"]
FIELDS = ["source", "topic_id", "quarters_ok_product", "quarters_ok_all",
          "delta_product_pp", "delta_all_pp", "difference_pp", "sample_ok"]


def window_composition(mentions: dict, window: list[str]) -> dict[str, float]:
    """구간 안에서 주제 간 구성비(%). 분모는 그 구간 전체 주제 언급 문서 수 합이다."""
    total = sum(mentions[(t, q)] for t in trend.TREND_TOPICS for q in window)
    if not total:
        return {t: 0.0 for t in trend.TREND_TOPICS}
    return {t: 100 * sum(mentions[(t, q)] for q in window) / total for t in trend.TREND_TOPICS}


def measure(run_dirs: list[Path], panel: dict[str, str], source: str):
    videos = trend.load_videos(run_dirs, panel)
    mentions, _channels, _docs, _raw = trend.count_mentions(run_dirs, videos, source)
    quarters = sorted({m["quarter"] for m in videos.values()})
    ok = {t: sum(1 for q in quarters if mentions[(t, q)] >= trend.MIN_DOCUMENT_COUNT)
          for t in trend.TREND_TOPICS}
    recent, prior = window_composition(mentions, RECENT), window_composition(mentions, PRIOR)
    delta = {t: recent[t] - prior[t] for t in trend.TREND_TOPICS}
    return len(videos), ok, delta


def run(run_dirs: list[Path], panel_csv: Path, out: Path) -> list[dict]:
    base = trend.load_panel(panel_csv)
    every = {k: "product" for k in base}          # expert 를 분모에 넣은 대조군

    rows = []
    summary = []
    for source in ("video", "comment"):
        n_a, ok_a, d_a = measure(run_dirs, base, source)
        n_b, ok_b, d_b = measure(run_dirs, every, source)
        summary.append((source, n_a, n_b))
        for topic in trend.TREND_TOPICS:
            rows.append({
                "source": source,
                "topic_id": topic,
                "quarters_ok_product": ok_a[topic],
                "quarters_ok_all": ok_b[topic],
                "delta_product_pp": round(d_a[topic], 2),
                "delta_all_pp": round(d_b[topic], 2),
                "difference_pp": round(d_b[topic] - d_a[topic], 2),
                # 충족 분기가 절반 미만이면 애초에 판정 대상이 아니다
                "sample_ok": "true" if ok_a[topic] >= 7 else "false",
            })

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    buf = io.StringIO()
    n_a, n_b = summary[0][1], summary[0][2]
    print(f"선크림 장문 모집단 : product 34채널 {n_a:,}편 -> 43채널 전부 {n_b:,}편", file=buf)
    print(file=buf)
    judged = [r for r in rows if r["sample_ok"] == "true"]
    # 부호만 보면 0 근처를 오가는 셀이 전부 뒤집힘으로 잡힌다. 한쪽이라도 실제로
    # 움직인 경우만 센다. MATERIAL 은 관측된 3년 변화량 범위(-5.5 ~ +2.6%p)에서
    # 눈에 보이는 최소 폭으로 잡았다.
    MATERIAL = 0.5
    flipped = [r for r in judged
               if r["delta_product_pp"] * r["delta_all_pp"] < 0
               and max(abs(r["delta_product_pp"]), abs(r["delta_all_pp"])) >= MATERIAL]
    noise = [r for r in judged
             if r["delta_product_pp"] * r["delta_all_pp"] < 0 and r not in flipped]
    worst = max(judged, key=lambda r: abs(r["difference_pp"]))
    print(f"판정 대상 셀 {len(judged)}개 중 방향이 뒤집힌 것 : {len(flipped)}개", file=buf)
    print(f"최대 차이 {abs(worst['difference_pp']):.2f}%p "
          f"({worst['source']} / {worst['topic_id']})", file=buf)
    for r in flipped:
        print(f"  뒤집힘 {r['source']} / {r['topic_id']} : "
              f"{r['delta_product_pp']:+.2f} -> {r['delta_all_pp']:+.2f}", file=buf)
    for r in noise:
        print(f"  0 근처 부호 변동(무시) {r['source']} / {r['topic_id']} : "
              f"{r['delta_product_pp']:+.2f} -> {r['delta_all_pp']:+.2f}", file=buf)
    print(file=buf)
    print("패널 선택이 판정 결론을 바꾸지 않는다. 현행 분류를 그대로 쓴다." if not flipped
          else "판정 대상에서 뒤집힘이 있으므로 필터 민감 신호로 표시해야 한다.", file=buf)
    print(buf.getvalue(), end="")
    return rows


def demo() -> None:
    m = {("백탁", "2025Q3"): 3, ("발림성", "2025Q3"): 1}
    for t in trend.TREND_TOPICS:
        m.setdefault(("백탁", "2025Q3"), 0)
    comp = window_composition(
        {**{(t, q): 0 for t in trend.TREND_TOPICS for q in RECENT}, **m}, RECENT)
    assert abs(comp["백탁"] - 75.0) < 1e-9, comp["백탁"]
    assert abs(comp["발림성"] - 25.0) < 1e-9, comp["발림성"]
    assert abs(sum(comp.values()) - 100.0) < 1e-9, "구성비 합은 100이어야 한다"
    empty = window_composition({(t, q): 0 for t in trend.TREND_TOPICS for q in RECENT}, RECENT)
    assert set(empty.values()) == {0.0}, "분모가 0이면 0으로 두고 나누지 않는다"
    print("demo ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dirs", nargs="*", type=Path)
    parser.add_argument("--panel", type=Path, default=Path("seeds/channels_v1.csv"))
    parser.add_argument("--out", type=Path, default=Path("reports/panel_sensitivity.csv"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if args.demo:
        demo()
        return 0
    if not args.run_dirs:
        parser.error("run_dirs 를 하나 이상 지정하거나 --demo 를 쓴다")

    run(args.run_dirs, args.panel, args.out)
    print(f"{args.out} 저장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
