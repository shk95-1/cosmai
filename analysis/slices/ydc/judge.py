#!/usr/bin/env python3
"""트렌드 유형 판정과 기회 점수. (A2~A4)

`trend.py`가 낸 지표 CSV를 읽어 판정 컬럼을 붙인다. 지표 계산과 판정을 분리한
이유는, 판정 기준(tau, 가중치, 유형 이름)이 팀 합의로 바뀔 수 있고 그때
지표를 다시 계산할 필요가 없기 때문이다.

정의는 TEAM_DECISIONS_v0.2 §3 을 따른다.

    evidence_strength  = 유효 근거 수 43.75% + 고유 채널 수 31.25% + 비중복 비율 25%
    opportunity_score  = velocity 0.35 + persistence 0.25
                       + channel_diffusion 0.20 + evidence_strength 0.20
    tau                = 0.35 (log 차이)
    유형 7종           급상승 / 사라짐 / 지속 인기 / 단기 피크 / 신규 등장 /
                       채널 확산 / 근거 부족  (미해당은 판정 보류)

사용법:
    python judge.py reports/trend_sunscreen_v0.2.csv --out reports/trend_judgement_v0.2.csv
"""
from __future__ import annotations

import argparse
import bisect
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

TAU = 0.35

# 채널 확산 임계값. 전년 동분기 대비 channel_diffusion 이 이만큼은 올라야 확산으로 본다.
#
# 처음에는 "0보다 크면 상승"으로 뒀는데 판정된 89셀 중 52셀(58%)이 여기로 쏠렸다.
# 아무리 작은 증가도 참이 되기 때문이다. 유형이 한 곳에 몰리면 분류의 정보량이 없다.
#
# 값은 tau 와 같은 방법으로 뽑았다 — 관측된 전년 동분기 대비 변화량 234셀의
# 절대값 75분위가 0.089 다(중앙 0.042 · 90분위 0.496). 이 컷에서 52셀이 14셀로
# 줄고, 남는 것은 +0.10 ~ +0.54 의 실제 도약이다.
#
# tau 와 마찬가지로 **소스마다 다시 뽑아야 한다.** NAVER·커머스가 붙으면 그 소스의
# 분포에서 새로 뽑고, 적용한 값을 결과 파일에 남긴다. 지금은 유튜브 실측값이다.
DIFFUSION_TAU = 0.089
MIN_DOCUMENT_COUNT = 5
NEW_TOPIC_MAX_SHARE = 0.01      # 신규 등장: 직전 3분기 구성비가 모두 이 미만
EVIDENCE_FLOOR = 50.0           # 근거 부족 컷
W_EVIDENCE = {"documents": 43.75, "channels": 31.25, "unique": 25.0}
W_SCORE = {"velocity": 0.35, "persistence": 0.25,
           "channel_diffusion": 0.20, "evidence_strength": 0.20}

OUT_FIELDS = [
    "quarter", "topic_id", "source", "document_count", "composition",
    "velocity_yoy", "persistence", "persistence_count", "channel_diffusion",
    "unique_ratio", "evidence_strength", "opportunity_score", "trend_type",
    "gap_pp", "hold_reason", "single_source", "judged", "tau", "diffusion_tau", "metric_version",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def percentile_rank(sorted_values: list[int], value: int) -> float:
    """그 소스 안에서 value 가 놓인 위치(0~1). 같은 값이 여럿이면 그 구간의 중간을 준다."""
    if len(sorted_values) <= 1:
        return 1.0
    below = bisect.bisect_left(sorted_values, value)
    upto = bisect.bisect_right(sorted_values, value)
    return ((below + upto) / 2) / len(sorted_values)


def evidence_strength(doc_rank: float, channel_ratio: float, unique_ratio: float) -> float:
    """0~100. 세 항을 각각 0~1로 두고 가중합한다.

    유효 근거 수는 **소스 내 백분위**로 넣는다. 소스별로 스케일이 완전히 다르고
    (영상 중앙 16 대 댓글 중앙 62) 절대 기준을 하나 쓰면 영상 셀이 전부 낮게
    나온다. 포화점을 잡는 방식도 써봤지만, 포화점을 어디에 두느냐로 `근거 부족`
    판정 개수가 크게 흔들려서 자의적이었다. 백분위는 그 소스 안에서 스스로
    보정된다.

    비중복 비율은 이 데이터에서 사실상 상수다(중앙 1.0, 최저 0.9939). 즉 25점이
    모든 셀에 동일하게 들어간다. 항을 빼지 않은 이유는 NAVER·커머스처럼 재게시가
    많은 소스가 붙으면 변별력이 생기기 때문이다. 지금은 정보가 없다는 사실을
    산출물에 기록한다.
    """
    return (W_EVIDENCE["documents"] * min(1.0, doc_rank)
            + W_EVIDENCE["channels"] * min(1.0, channel_ratio)
            + W_EVIDENCE["unique"] * min(1.0, unique_ratio))


def classify(row: dict[str, Any], history: dict[str, dict[str, Any]],
             quarters: list[str], is_last: bool) -> str:
    """판정 순서 — 위에서 먼저 걸리면 종료."""
    if is_last:
        return "미확정(진행 중)"

    doc_count = row["document_count"]
    if row["evidence_strength"] < EVIDENCE_FLOOR or doc_count < MIN_DOCUMENT_COUNT:
        return "근거 부족"

    index = quarters.index(row["quarter"])
    prior3 = quarters[max(0, index - 3):index]
    if (prior3 and all(history[q]["composition"] < NEW_TOPIC_MAX_SHARE for q in prior3)
            and doc_count >= MIN_DOCUMENT_COUNT and row["channel_count"] >= 2):
        return "신규 등장"

    velocity = row["velocity_yoy"]
    if velocity is None:
        return "판정 보류"          # 전년 동분기 표본이 없어 변화를 계산할 수 없다

    persistence_count = row["persistence_count"]
    if velocity > TAU:
        return "단기 피크" if persistence_count == 1 else "급상승"

    prev_year = f"{int(row['quarter'][:4]) - 1}Q{row['quarter'][5]}"
    prev = history.get(prev_year)
    if (prev and row["channel_diffusion"] - prev["channel_diffusion"] > DIFFUSION_TAU
            and velocity <= TAU):
        return "채널 확산"

    if abs(velocity) <= TAU and persistence_count >= 3:
        return "지속 인기"

    peak = max(history[q]["composition"] for q in quarters)
    if velocity < -TAU and row["composition"] < peak / 2:
        return "사라짐"

    return "판정 보류"


def hold_reason(row: dict[str, Any], history: dict[str, dict[str, Any]],
                quarters: list[str]) -> str:
    """`판정 보류`가 나온 이유. 보류를 그냥 빈칸으로 두면 규칙의 구멍이 안 보인다.

    실제로 이 컬럼이 하나를 드러냈다. 톤업·메이크업베이스 댓글은 velocity -0.56 으로
    이번 데이터에서 가장 큰 하락인데, `사라짐`이 velocity < -tau **와**
    구성비 < 최고 분기/2 를 함께 요구하므로 보류로 떨어진다. 유형을 늘리는 것은
    팀 합의 사항이므로 규칙은 그대로 두고 사유만 남긴다.
    """
    velocity = row["velocity_yoy"]
    if velocity is None:
        return "전년 동분기 표본 부족"
    peak = max(history[q]["composition"] for q in quarters)
    if velocity < -TAU:
        return f"하락하지만 최고 분기({peak * 100:.1f}%)의 절반 이상"
    if abs(velocity) <= TAU and row["persistence_count"] < 3:
        return "변화가 tau 이내이나 지속성 부족"
    return "규칙 미해당"


def judge(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        by_source[raw["source"]].append({
            "quarter": raw["quarter"],
            "topic_id": raw["topic_id"],
            "source": raw["source"],
            "document_count": int(raw["document_count"]),
            "composition": float(raw["composition"]),
            "velocity_yoy": as_float(raw.get("velocity_yoy")),
            "persistence": float(raw["persistence"]),
            "persistence_count": int(raw["persistence_count"]),
            "channel_count": int(raw["channel_count"]),
            "panel_channels": int(raw["panel_channels"]),
            "channel_diffusion": float(raw["channel_diffusion"]),
            "unique_ratio": float(raw["unique_ratio"]),
            "metric_version": raw["metric_version"],
        })

    notes: dict[str, Any] = {"tau": TAU, "diffusion_tau": DIFFUSION_TAU, "sources": {}}
    out: list[dict[str, Any]] = []

    for source, cells in by_source.items():
        quarters = sorted({c["quarter"] for c in cells})
        last = quarters[-1]

        counts_sorted = sorted(c["document_count"] for c in cells)
        for cell in cells:
            cell["evidence_strength"] = round(evidence_strength(
                percentile_rank(counts_sorted, cell["document_count"]),
                cell["channel_count"] / cell["panel_channels"] if cell["panel_channels"] else 0.0,
                cell["unique_ratio"]), 1)

        history: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for cell in cells:
            history[cell["topic_id"]][cell["quarter"]] = cell

        for cell in cells:
            cell["trend_type"] = classify(
                cell, history[cell["topic_id"]], quarters, cell["quarter"] == last)
            cell["hold_reason"] = (hold_reason(cell, history[cell["topic_id"]], quarters)
                                   if cell["trend_type"] == "판정 보류" else "")

        # opportunity_score — 네 항을 0~1로 맞춘 뒤 가중합하고, 제품군 내 0~100 정규화
        scored = [c for c in cells
                  if c["velocity_yoy"] is not None and c["quarter"] != last
                  and c["trend_type"] not in ("근거 부족", "판정 보류")]
        if scored:
            vs = [c["velocity_yoy"] for c in scored]
            lo, hi = min(vs), max(vs)
            span = (hi - lo) or 1.0
            for cell in cells:
                cell["opportunity_score"] = None
            raws = []
            for cell in scored:
                value = (W_SCORE["velocity"] * (cell["velocity_yoy"] - lo) / span
                         + W_SCORE["persistence"] * cell["persistence"]
                         + W_SCORE["channel_diffusion"] * cell["channel_diffusion"]
                         + W_SCORE["evidence_strength"] * cell["evidence_strength"] / 100)
                raws.append((cell, value))
            values = [v for _c, v in raws]
            rlo, rhi = min(values), max(values)
            rspan = (rhi - rlo) or 1.0
            for cell, value in raws:
                cell["opportunity_score"] = round(100 * (value - rlo) / rspan, 1)
        else:
            for cell in cells:
                cell["opportunity_score"] = None

        notes["sources"][source] = {
            "cells": len(cells),
            "document_count_scale": "소스 내 백분위",
            "scored_cells": len(scored),
            "unique_ratio_median": round(statistics.median(c["unique_ratio"] for c in cells), 4),
        }
        out.extend(cells)

    # gap = 댓글 구성비 - 영상 구성비. (주제, 분기) 단위이므로 두 행에 같은 값을 적는다.
    comp: dict[tuple[str, str], float] = {
        (c["topic_id"], c["quarter"]): c["composition"] for c in out
        if c["source"] == "youtube_comment"}
    vid: dict[tuple[str, str], float] = {
        (c["topic_id"], c["quarter"]): c["composition"] for c in out
        if c["source"] == "youtube_video"}
    for cell in out:
        key = (cell["topic_id"], cell["quarter"])
        if key in comp and key in vid:
            cell["gap_pp"] = round(100 * (comp[key] - vid[key]), 2)
        else:
            cell["gap_pp"] = None
        # YouTube 안에서 영상과 댓글은 상호 검증 소스가 아니라 성격이 다른 두 계열이다.
        # source_count >= 2 는 플랫폼 간 통합 판정에만 적용하고, 여기서는 표시만 한다.
        cell["single_source"] = "true"
        cell["judged"] = "true" if cell["trend_type"] not in (
            "근거 부족", "판정 보류", "미확정(진행 중)") else "false"
        cell["tau"] = TAU
        cell["diffusion_tau"] = DIFFUSION_TAU

    out.sort(key=lambda c: (c["source"], c["topic_id"], c["quarter"]))
    return out, notes


def demo() -> None:
    # evidence_strength: 세 항이 만점이면 100
    assert abs(evidence_strength(1.0, 1.0, 1.0) - 100.0) < 1e-9
    # 근거 수만 0이면 나머지 두 항 합만 남는다
    assert abs(evidence_strength(0.0, 1.0, 1.0) - 56.25) < 1e-9
    # 백분위는 1을 넘지 않는다
    assert evidence_strength(2.0, 0.5, 1.0) == evidence_strength(1.0, 0.5, 1.0)
    # 백분위 계산: 같은 값이 여럿이면 그 구간 중간
    assert percentile_rank([1, 2, 3, 4], 1) == 0.125
    assert percentile_rank([1, 2, 3, 4], 4) == 0.875
    assert percentile_rank([5, 5, 5, 5], 5) == 0.5
    assert percentile_rank([7], 7) == 1.0

    # 보류 사유
    qs = ["2024Q1", "2024Q2"]
    h = {"2024Q1": {"composition": 0.30, "velocity_yoy": None, "persistence_count": 0},
         "2024Q2": {"composition": 0.20, "velocity_yoy": -0.9, "persistence_count": 0}}
    assert hold_reason(h["2024Q1"], h, qs) == "전년 동분기 표본 부족"
    assert "절반 이상" in hold_reason(h["2024Q2"], h, qs)
    h["2024Q2"] = {"composition": 0.20, "velocity_yoy": 0.1, "persistence_count": 1}
    assert hold_reason(h["2024Q2"], h, qs) == "변화가 tau 이내이나 지속성 부족"

    quarters = ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1"]

    def cell(q, **kw):
        base = dict(quarter=q, document_count=20, composition=0.10, velocity_yoy=0.0,
                    persistence=1.0, persistence_count=4, channel_count=5,
                    channel_diffusion=0.5, evidence_strength=80.0)
        base.update(kw)
        return base

    hist = {q: cell(q) for q in quarters}

    # 마지막 분기는 확정하지 않는다
    assert classify(cell("2025Q1"), hist, quarters, True) == "미확정(진행 중)"
    # 근거 컷
    assert classify(cell("2024Q4", evidence_strength=49.0), hist, quarters, False) == "근거 부족"
    assert classify(cell("2024Q4", document_count=4), hist, quarters, False) == "근거 부족"
    # velocity 없으면 판정 보류
    assert classify(cell("2024Q4", velocity_yoy=None), hist, quarters, False) == "판정 보류"
    # 급상승 vs 단기 피크는 persistence_count 로 갈린다
    assert classify(cell("2024Q4", velocity_yoy=0.5, persistence_count=1),
                    hist, quarters, False) == "단기 피크"
    assert classify(cell("2024Q4", velocity_yoy=0.5, persistence_count=2),
                    hist, quarters, False) == "급상승"
    # tau 경계: 정확히 tau 면 급상승이 아니다
    assert classify(cell("2024Q4", velocity_yoy=TAU, persistence_count=4),
                    hist, quarters, False) != "급상승"
    # 지속 인기
    assert classify(cell("2024Q4", velocity_yoy=0.0, persistence_count=3),
                    hist, quarters, False) == "지속 인기"
    # 채널 확산: 전년 동분기보다 확산이 커지고 velocity 는 tau 이하
    hist_dif = dict(hist)
    hist_dif["2023Q4"] = cell("2023Q4", channel_diffusion=0.2)
    q2 = ["2023Q4"] + quarters
    assert classify(cell("2024Q4", velocity_yoy=0.1, channel_diffusion=0.9,
                         persistence_count=1), hist_dif, q2, False) == "채널 확산"
    # 임계값 미만의 미세한 증가는 확산으로 보지 않는다.
    # 이 검사가 없으면 아무리 작은 증가도 참이 되어 판정이 한 유형으로 쏠린다.
    hist_small = dict(hist)
    hist_small["2023Q4"] = cell("2023Q4", channel_diffusion=0.50 - DIFFUSION_TAU / 2)
    assert classify(cell("2024Q4", velocity_yoy=0.1, channel_diffusion=0.50,
                         persistence_count=1), hist_small, q2, False) != "채널 확산"
    # 사라짐: 최고 분기의 절반 미만
    hist_fall = {q: cell(q, composition=0.20) for q in quarters}
    hist_fall["2024Q4"] = cell("2024Q4", composition=0.05, velocity_yoy=-0.9)
    assert classify(hist_fall["2024Q4"], hist_fall, quarters, False) == "사라짐"
    # 신규 등장: 직전 3분기 구성비가 모두 1% 미만
    hist_new = {q: cell(q, composition=0.001) for q in quarters}
    hist_new["2024Q4"] = cell("2024Q4", composition=0.08)
    assert classify(hist_new["2024Q4"], hist_new, quarters, False) == "신규 등장"
    print("demo ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("metrics_csv", nargs="?", type=Path,
                        default=Path("reports/trend_sunscreen_v0.2.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("reports/trend_judgement_v0.2.csv"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if args.demo:
        demo()
        return 0

    rows, notes = judge(read_rows(args.metrics_csv))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_FIELDS,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        counts[row["source"]][row["trend_type"]] += 1
    print(f"{args.out} : {len(rows)}행  (tau={TAU}, diffusion_tau={DIFFUSION_TAU})")
    for source, tally in counts.items():
        info = notes["sources"][source]
        print(f"\n[{source}] 셀 {info['cells']}개, 근거 수 척도={info['document_count_scale']}, "
              f"비중복 중앙 {info['unique_ratio_median']} (변별력 없음)")
        for kind, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"   {kind:<16} {n:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
