#!/usr/bin/env python3
"""커머스 랭킹 시계열 탐색. 6일치로 무엇을 볼 수 있고 무엇을 볼 수 없나.

배경. `MASTER_REPORT` §3-1 은 트렌드를 횡단면 구조로 재정의했고 근거가 이랬다 —
*"랭킹 스냅샷이 2.7일뿐이라 시간에 따른 상승은 어떻게 계산해도 신뢰할 수 없었다."*

그 전제가 바뀌었다. 08.24 현재 `rank_snapshot` 이 171,030행이고 네 소스 모두
**6일 · 시간당 스냅샷 61~86개**다. 수집기가 계속 돌고 있다. 그래서 시간축을
쓸 수 있는지 실제로 재본다.

**6일로 분기 트렌드를 볼 수는 없다.** 우리 유튜브 지표는 전년 동분기 비교이고
계절성을 상쇄하려고 그렇게 만들었다. 커머스 6일에 같은 규칙을 쓰면 아무 의미가
없다. 6일로 볼 수 있는 것은 다른 것이다.

    순위 변동 폭     같은 제품이 6일 안에 몇 계단 움직이나
    신규 진입        첫날에 없고 마지막 날에 있는 제품
    이탈             첫날에 있고 마지막 날에 없는 제품
    관측 밀도        스냅샷 간격이 고른가, 구멍이 있나

관측 밀도를 먼저 보는 이유. 간격이 들쭉날쭉하면 "6일"이 6일치 관측이 아니다.
화해가 6시간 끊긴 이력이 이미 있다(MANIFEST). 밀도를 안 보고 변동을 재면
수집 공백을 순위 안정으로 읽는다.

선케어만 본다. 소스마다 분류 이름이 달라 `board` 와 `category_name` 을 같이 본다.

    oliveyoung   board = suncare
    glowpick     category_name 에 선크림·선케어·선블록
    daisomall    category_name = 뷰티/위생 (더 잘게 없다 — 아래 한계 참조)
    hwahae       category_name 에 선케어 계열

사용법:
    python commerce_ranking.py                 # 서버에서 받아 캐시하고 분석
    python commerce_ranking.py --offline       # 캐시만 쓴다
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

BASE = "http://100.106.220.24:3000"
PAGE = 1000
COLUMNS = ("source,board,category_key,category_name,product_key,product_name,"
           "brand,rank,rank_delta,is_new,review_count,price,captured_at")
# 전순서 정렬. 이 조합이 유일하지 않으면 페이징이 다시 흔들린다
ORDER = ("captured_at.asc,source.asc,board.asc,category_key.asc,"
         "rank.asc,product_key.asc")
SUN_RE = re.compile(r"선크림|선케어|선블록|선스틱|suncare", re.I)
FIELDS = ["source", "board", "product_key", "product_name", "brand",
          "snapshots", "first_rank", "last_rank", "best_rank", "worst_rank",
          "swing", "moved", "entered", "left"]


def fetch(cache: Path, offline: bool, freeze: str) -> list[dict]:
    """랭킹 전량. **정지 + 정렬 + 행수 대조** 없이 offset 페이징을 하면 안 된다.

    처음에 이걸 안 하고 크게 틀렸다. 정렬 없이 `limit/offset` 으로 받는 동안
    수집기가 계속 쓰고 있었다. Postgres 는 `ORDER BY` 가 없으면 순서를 보장하지
    않고, 그 위에 유입까지 겹치면 같은 offset 이 다른 행을 가리킨다. 결과가 이랬다.

      - 같은 행이 여러 번 들어와 **완전 중복 37%** 로 보였다
      - 다른 행은 통째로 빠져 스냅샷이 얕게 보이고, 그게 **관측 깊이 변동**으로 보였다

    둘 다 서버에 없는 현상이었다. 지목한 슬라이스를 직접 조회하니 1행이었고,
    정지 집합을 정렬해 다시 받으니 중복 0건 · 보드 22개 중 21개 깊이 고정이었다.
    (현준님이 재현이 안 된다며 이 가설을 먼저 제기해 주셔서 잡았다.)

    그래서 세 가지를 건다.
      1. `captured_at < freeze` 로 작업 집합을 **정지**시킨다
      2. 유일한 키로 **전순서 정렬**한다
      3. 받은 행수를 서버의 `count=exact` 와 **대조**하고 어긋나면 멈춘다
    """
    if cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
        if offline:
            print(f"캐시 {len(rows):,}행 (--offline)")
            return rows
    if offline:
        raise SystemExit(f"캐시가 없다: {cache}")

    where = f"captured_at=lt.{urllib.parse.quote(freeze)}"
    head = urllib.request.Request(
        f"{BASE}/rank_snapshot?{where}&select=source",
        headers={"Accept": "application/json", "Prefer": "count=exact",
                 "Range": "0-0"})
    with urllib.request.urlopen(head, timeout=60) as handle:
        expected = int(handle.headers["Content-Range"].split("/")[1])

    rows, offset = [], 0
    while True:
        url = (f"{BASE}/rank_snapshot?{where}&select={COLUMNS}&order={ORDER}"
               f"&limit={PAGE}&offset={offset}")
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as handle:
            batch = json.load(handle)
        if not batch:
            break
        rows.extend(batch)
        offset += PAGE

    if len(rows) != expected:
        raise SystemExit(
            f"행수가 어긋난다 — 서버 {expected:,} vs 받은 것 {len(rows):,}. "
            f"페이징 중에 집합이 변했다. freeze 를 더 과거로 잡아야 한다.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"정지 시각 {freeze} 이전 {len(rows):,}행 (서버 행수와 일치) → {cache}")
    return rows


def is_suncare(row: dict) -> bool:
    return bool(SUN_RE.search(f"{row.get('board') or ''} {row.get('category_name') or ''}"))


def dedupe(rows: list[dict]) -> list[dict]:
    """완전 중복 행을 뺀다. **정상이면 0건이어야 한다.**

    한때 37% 가 중복으로 보였는데 서버가 아니라 우리 페이징 문제였다(`fetch` 주석).
    정지·정렬로 받으면 0건이다. 그래도 이 함수를 남기는 이유는, 0 이 아니면
    추출이 잘못됐다는 신호이기 때문이다 — `run` 이 그 수를 찍는다.
    """
    seen = set()
    out = []
    for row in rows:
        key = (row["source"], row["board"], row["category_key"],
               row["captured_at"], row["rank"], row["product_key"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def depth_guard(rows: list[dict], depth: int) -> list[dict]:
    """관측 깊이를 맞춘다.

    한때 `oliveyoung suncare` 의 깊이가 22위~100위로 들쭉날쭉해 보였는데, 그것도
    페이징 표류였다. 정지·정렬로 받으면 보드 22개 중 21개가 깊이 고정이고
    suncare 는 85개 스냅샷 전부 100위다. 남은 변동은 daisomall sale_rising
    한 보드(92~100)뿐이다.

    그래도 이 함수를 남긴다. 수집기가 부분 응답을 적재하면 언제든 다시 생기는
    현상이고, 보드마다 목표 깊이가 다르므로(glowpick 20 · hwahae 9 · 나머지 100)
    비교 구간을 맞추는 일 자체는 여전히 필요하다.

    두 가지를 동시에 건다.
      1. `depth` 위까지 실제로 관측한 스냅샷만 남긴다
      2. 그 스냅샷 안에서도 `depth` 이내 순위만 본다
    """
    reached: dict[tuple, int] = defaultdict(int)
    for row in rows:
        if row.get("rank") is None:
            continue
        key = (row["source"], row["board"], row["category_key"], row["captured_at"])
        reached[key] = max(reached[key], int(row["rank"]))

    out = []
    for row in rows:
        if row.get("rank") is None or int(row["rank"]) > depth:
            continue
        key = (row["source"], row["board"], row["category_key"], row["captured_at"])
        if reached[key] < depth:
            continue                        # 이 스냅샷은 depth 까지 못 봤다
        out.append(row)
    return out


def density(rows: list[dict]) -> None:
    """스냅샷 간격. 구멍을 안 보고 변동을 재면 수집 공백을 안정으로 읽는다."""
    per = defaultdict(set)
    for row in rows:
        per[row["source"]].add(row["captured_at"][:16])
    print(f"{'소스':<12}{'스냅샷':>7}{'날짜':>6}{'중앙 간격':>10}{'최대 공백':>10}  구간")
    for source, stamps in sorted(per.items()):
        times = sorted(stamps)
        days = {t[:10] for t in times}
        gaps = []
        for a, b in zip(times, times[1:]):
            # 시각 문자열만으로 시간 차를 낸다. 날짜 경계만 넘기면 되므로 충분하다
            ha, hb = int(a[11:13]), int(b[11:13])
            gaps.append((hb - ha) % 24 or 24 if a[:10] != b[:10] else hb - ha)
        print(f"{source:<12}{len(times):>7}{len(days):>6}"
              f"{statistics.median(gaps) if gaps else 0:>9.0f}시"
              f"{max(gaps) if gaps else 0:>9}시  {min(days)} ~ {max(days)}")


def track(rows: list[dict]) -> list[dict]:
    """제품별 순위 시계열. 키는 (source, board, category_key, product_key)."""
    series: dict[tuple, list[tuple[str, int]]] = defaultdict(list)
    label: dict[tuple, dict] = {}
    stamps_by_source: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("rank") is None:
            continue
        key = (row["source"], row["board"], row["category_key"], row["product_key"])
        series[key].append((row["captured_at"], int(row["rank"])))
        label[key] = row
        stamps_by_source[row["source"]].add(row["captured_at"])

    first_stamp = {s: min(v) for s, v in stamps_by_source.items()}
    last_stamp = {s: max(v) for s, v in stamps_by_source.items()}

    out = []
    for key, points in series.items():
        points.sort()
        ranks = [r for _t, r in points]
        source = key[0]
        row = label[key]
        # 첫 스냅샷에 없고 마지막에 있으면 진입, 반대면 이탈.
        # 스냅샷 하나만 있는 제품은 둘 다 false 다 — 판단 근거가 없다
        seen = {t for t, _r in points}
        entered = first_stamp[source] not in seen and last_stamp[source] in seen
        left = first_stamp[source] in seen and last_stamp[source] not in seen
        out.append({
            "source": source, "board": key[1],
            "product_key": key[3],
            "product_name": (row.get("product_name") or "")[:60],
            "brand": row.get("brand") or "",
            "snapshots": len(points),
            "first_rank": ranks[0], "last_rank": ranks[-1],
            "best_rank": min(ranks), "worst_rank": max(ranks),
            "swing": max(ranks) - min(ranks),
            "moved": ranks[-1] - ranks[0],
            "entered": "true" if entered else "",
            "left": "true" if left else "",
        })
    return out


def report(tracks: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(tracks, key=lambda r: (r["source"], r["last_rank"])))

    print()
    print(f"{'소스':<12}{'제품':>7}{'중앙 변동폭':>12}{'움직임 없음':>12}"
          f"{'진입':>6}{'이탈':>6}")
    per_source = defaultdict(list)
    for row in tracks:
        per_source[row["source"]].append(row)
    for source, group in sorted(per_source.items()):
        swings = [r["swing"] for r in group]
        still = sum(1 for r in group if r["swing"] == 0)
        print(f"{source:<12}{len(group):>7}{statistics.median(swings):>12.0f}"
              f"{100 * still / len(group):>11.0f}%"
              f"{sum(1 for r in group if r['entered']):>6}"
              f"{sum(1 for r in group if r['left']):>6}")

    movers = [r for r in tracks if r["snapshots"] >= 10]
    movers.sort(key=lambda r: r["moved"])
    print()
    print("가장 많이 올라간 제품 (스냅샷 10개 이상)")
    for r in movers[:8]:
        print(f"  {r['source']:<11}{r['first_rank']:>4} -> {r['last_rank']:<4}"
              f"({r['moved']:+d})  {r['brand']:<10}{r['product_name'][:38]}")
    print("가장 많이 내려간 제품")
    for r in reversed(movers[-8:]):
        print(f"  {r['source']:<11}{r['first_rank']:>4} -> {r['last_rank']:<4}"
              f"({r['moved']:+d})  {r['brand']:<10}{r['product_name'][:38]}")
    print()
    print(f"{out} 저장")


def run(cache: Path, out: Path, offline: bool, all_categories: bool, depth: int,
        freeze: str) -> int:
    rows = fetch(cache, offline, freeze)
    before = len(rows)
    rows = dedupe(rows)
    dropped = before - len(rows)
    print(f"완전 중복 {dropped:,}행"
          + ("" if dropped == 0 else f" — 0 이 아니면 추출이 잘못됐다는 신호다"))
    if not all_categories:
        picked = [r for r in rows if is_suncare(r)]
        print(f"선케어 필터 {len(rows):,} -> {len(picked):,}행")
        by_source = Counter(r["source"] for r in picked)
        for source, n in by_source.most_common():
            print(f"  {source:<12}{n:>9,}")
        missing = {r["source"] for r in rows} - set(by_source)
        if missing:
            print(f"  선케어 분류가 없는 소스: {', '.join(sorted(missing))}")
        rows = picked
    if not rows:
        print("해당 행이 없다")
        return 1
    print()
    density(rows)

    print()
    print("=== 깊이 보정 없음 ===")
    report(track(rows), out.with_name(out.stem + "_raw.csv"))

    guarded = depth_guard(rows, depth)
    print()
    print(f"=== 상위 {depth}위까지 관측한 스냅샷만 · {depth}위 이내만 "
          f"({len(rows):,} -> {len(guarded):,}행) ===")
    if not guarded:
        print(f"상위 {depth}위까지 관측한 스냅샷이 없다. --depth 를 낮춘다")
        return 1
    report(track(guarded), out)
    return 0


def demo() -> None:
    assert is_suncare({"board": "suncare", "category_name": ""})
    assert is_suncare({"board": "category", "category_name": "01 > 선케어 > 선블록"})
    assert is_suncare({"board": "category", "category_name": "선크림"})
    assert not is_suncare({"board": "makeup", "category_name": "01 > 스킨케어 > 크림"})
    assert not is_suncare({"board": "review", "category_name": "뷰티/위생"})

    rows = [
        {"source": "s", "board": "b", "category_key": "1", "product_key": "p",
         "product_name": "가", "brand": "브", "rank": 10,
         "captured_at": "2026-08-19T00:00", "rank_delta": None, "is_new": None},
        {"source": "s", "board": "b", "category_key": "1", "product_key": "p",
         "product_name": "가", "brand": "브", "rank": 3,
         "captured_at": "2026-08-20T00:00", "rank_delta": None, "is_new": None},
        # 마지막 스냅샷에만 있는 제품 = 진입
        {"source": "s", "board": "b", "category_key": "1", "product_key": "q",
         "product_name": "나", "brand": "브", "rank": 8,
         "captured_at": "2026-08-20T00:00", "rank_delta": None, "is_new": None},
    ]
    got = {r["product_key"]: r for r in track(rows)}
    assert got["p"]["moved"] == -7 and got["p"]["swing"] == 7
    assert got["p"]["entered"] == "" and got["p"]["left"] == ""
    assert got["q"]["entered"] == "true", got["q"]
    # 순위는 작을수록 좋다. moved 가 음수면 올라간 것이다
    assert got["p"]["best_rank"] == 3 and got["p"]["worst_rank"] == 10

    # 완전 중복은 빠져야 한다. 남으면 변동 폭과 진입 수가 부푼다
    assert len(dedupe(rows + rows)) == len(rows)
    assert len(dedupe(rows)) == len(rows)

    # 깊이 보정 — 얕은 스냅샷은 통째로 빠져야 한다
    shallow = [
        {"source": "s", "board": "b", "category_key": "1", "product_key": "a",
         "rank": 1, "captured_at": "T1"},
        {"source": "s", "board": "b", "category_key": "1", "product_key": "b",
         "rank": 2, "captured_at": "T1"},
        {"source": "s", "board": "b", "category_key": "1", "product_key": "a",
         "rank": 1, "captured_at": "T2"},          # T2 는 1위까지만 봤다
    ]
    kept = depth_guard(shallow, 2)
    assert {r["captured_at"] for r in kept} == {"T1"}, kept
    # 깊이 밖 순위는 스냅샷이 충분히 깊어도 빠진다
    assert all(int(r["rank"]) <= 2 for r in depth_guard(shallow, 2))
    assert depth_guard(shallow, 5) == [], "아무 스냅샷도 5위까지 못 봤다"
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", type=Path, default=Path(".cache/rank_snapshot.json"))
    p.add_argument("--out", type=Path, default=Path("reports/commerce_ranking.csv"))
    p.add_argument("--offline", action="store_true", help="서버를 부르지 않는다")
    p.add_argument("--all", action="store_true", dest="all_categories",
                   help="선케어 필터를 걸지 않는다")
    p.add_argument("--depth", type=int, default=20,
                   help="관측 깊이 보정 기준 순위. 보드별 목표 깊이의 최소가 glowpick 20 이다")
    p.add_argument("--freeze", default="2026-08-24T00:00:00+00:00",
                   help="이 시각 이전만 받는다. 페이징 중 유입을 막는 장치다")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    return run(a.cache, a.out, a.offline, a.all_categories, a.depth, a.freeze)


if __name__ == "__main__":
    raise SystemExit(main())
