#!/usr/bin/env python3
"""전성분표의 두 가지 파싱 오류를 고친다. 원본은 덮어쓰지 않고 정규화된 사본을 만든다.

배경. 수호님이 식약처·올리브영을 매핑해 만든 `product_ingredient_function.csv` 는
중복 0행, `ingredient_order` 1..n 연속으로 구조가 깨끗하다. 다만 성분명 파싱에 두 가지
문제가 있어 그대로 집계하면 성분이 갈라지거나 사라진다.

  1. 성분명 안에 공백이 끼어든다 — `에칠헥실트 리아존`. 472행 · 164개 제품(28%).
     공백을 제거하면 성분 고유값이 2,182에서 1,972로 줄고 UV 차단 성분이 19종에서
     9종으로 합쳐진다. 정규화 없이 세면 같은 성분이 여러 종으로 갈라져 제품 수가
     과소 집계된다.
  2. 전성분이 한 셀에 뭉쳐 있다 — `정제수에칠헥실메톡시신나메이트디메치콘...`.
     28행 · 28개 제품(4.9%). 대부분 기획 세트라 여러 제품의 전성분이 이어붙었다.
     이 제품들은 지금 성분 정보가 사실상 없다.

2번은 같은 파일 안의 성분명 사전으로 최장일치 분해가 된다. 다만 완벽하지 않아서
**해독률이 임계값을 넘을 때만 채택하고, 못 넘으면 원본을 그대로 두고 표시만 한다.**
추측으로 쪼갠 성분을 집계에 넣으면 그게 더 나쁘다.

이 스크립트는 원본을 고치지 않는다. 근본 원인은 수집기의 파서이므로 이 결과와 함께
스크립트를 담당자에게 전달해 파서를 고치는 것이 맞다. 그때까지 우리는 사본을 쓴다.

사용법:
    python normalize_ingredients.py --input "<원본.csv>" --out reports/ingredient_normalized.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import statistics
from pathlib import Path

csv.field_size_limit(10 ** 8)

MAX_NAME = 40        # 이보다 길면 성분 하나가 아니라 뭉친 문자열로 본다
MIN_COVERAGE = 95.0  # 최장일치로 이 비율 이상 해독돼야 분해를 채택한다
FIELDS = ["product_name", "brand", "ingredient_order", "ingredient", "function", "fix"]


def norm(value: str | None) -> str:
    return (value or "").replace(" ", "")


def build_vocab(rows: list[dict]) -> list[str]:
    """길이가 정상인 성분명만 모아 사전을 만든다. 긴 것부터 봐야 최장일치가 된다."""
    names = {norm(r["ingredient"]) for r in rows}
    return sorted((n for n in names if 0 < len(n) <= MAX_NAME), key=len, reverse=True)


def segment(text: str, vocab: list[str]) -> tuple[list[str], int]:
    """사전 최장일치로 쪼갠다. 반환값은 (성분 목록, 해독 못 한 글자 수)."""
    found: list[str] = []
    i = unresolved = 0
    while i < len(text):
        for word in vocab:
            if text.startswith(word, i):
                found.append(word)
                i += len(word)
                break
        else:
            unresolved += 1
            i += 1
    return found, unresolved


def run(source: Path, out: Path) -> dict:
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig", newline="")))
    vocab = build_vocab(rows)
    stats = collections.Counter()
    coverages: list[float] = []
    fixed: list[dict] = []

    for row in rows:
        name = norm(row["ingredient"])
        had_space = " " in (row["ingredient"] or "").strip()

        if len(name) <= MAX_NAME:
            stats["공백 제거"] += bool(had_space)
            stats["그대로"] += not had_space
            fixed.append({**row, "ingredient": name,
                          "fix": "공백 제거" if had_space else ""})
            continue

        parts, miss = segment(name, vocab)
        coverage = 100 * (len(name) - miss) / len(name) if name else 0.0
        coverages.append(coverage)
        if coverage >= MIN_COVERAGE and parts:
            stats["뭉친 행 분해"] += 1
            stats["분해로 늘어난 행"] += len(parts) - 1
            base = int(row["ingredient_order"])
            for offset, part in enumerate(parts):
                fixed.append({**row, "ingredient_order": base + offset, "ingredient": part,
                              "fix": f"뭉친 행 분해 (해독률 {coverage:.0f}%)"})
        else:
            stats["분해 실패 — 원본 유지"] += 1
            fixed.append({**row, "ingredient": name,
                          "fix": f"분해 실패 (해독률 {coverage:.0f}%) — 집계에서 제외할 것"})

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as h:
        w = csv.DictWriter(h, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(fixed)

    before = len({norm(r["ingredient"]) for r in rows})
    after = len({r["ingredient"] for r in fixed if len(r["ingredient"]) <= MAX_NAME})
    print(f"원본 {len(rows):,}행 -> 정규화 {len(fixed):,}행")
    for k, v in stats.most_common():
        print(f"   {k:<24} {v:,}")
    print()
    print(f"성분 고유값 {before:,} -> {after:,}")
    if coverages:
        print(f"뭉친 행 해독률 중앙 {statistics.median(coverages):.1f}% "
              f"(최소 {min(coverages):.1f}% · 최대 {max(coverages):.1f}%)")
    print()
    print(f"{out} 저장")
    return dict(stats)


def demo() -> None:
    assert norm("에칠헥실트 리아존") == "에칠헥실트리아존"
    vocab = ["에칠헥실메톡시신나메이트", "티타늄디옥사이드", "정제수", "글리세린"]
    vocab.sort(key=len, reverse=True)
    parts, miss = segment("정제수티타늄디옥사이드글리세린", vocab)
    assert parts == ["정제수", "티타늄디옥사이드", "글리세린"], parts
    assert miss == 0
    # 사전에 없는 글자는 해독 실패로 세고 건너뛴다 — 추측해서 채우지 않는다
    parts, miss = segment("정제수ZZZ글리세린", vocab)
    assert parts == ["정제수", "글리세린"] and miss == 3, (parts, miss)
    # 최장일치라 짧은 이름이 긴 이름을 먹지 않는다
    v2 = sorted(["징크", "징크옥사이드"], key=len, reverse=True)
    assert segment("징크옥사이드", v2)[0] == ["징크옥사이드"]
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # 정본은 리포 안이다. 카톡 폴더를 기본값으로 두면 파일이 사라지는 순간
    # 스크립트가 재현되지 않는다. 실제로 한 번 사라졌다.
    p.add_argument("--input", type=Path,
                   default=Path("data/external/product_ingredient_function.csv"))
    p.add_argument("--out", type=Path, default=Path("reports/ingredient_normalized.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    if not a.input.exists():
        raise SystemExit(f"원본이 없다: {a.input}")
    run(a.input, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
