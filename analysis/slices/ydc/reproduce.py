#!/usr/bin/env python3
"""재실행 일치 검증. (기획안 08.25 — "동일 입력의 결과 일치, 입력·설정 버전 추적,
중복 누적 방지를 검증한다")

기획안은 재현성이 부족한 산출물은 최종 결과와 발표에서 빼라고 정해 뒀다. 그러면
"재현된다"를 말로 하지 않고 확인해야 한다. 여기서 확인하는 것 네 가지다.

1. **동일 입력 두 번 → 같은 출력.** 같은 run 으로 지표·판정을 두 번 계산해
   정규화 CSV 의 sha256 을 비교한다. 사전이 dict 순서에 의존하거나 집합 순회가
   결과에 새면 여기서 걸린다.

2. **커밋된 산출물이 지금 코드로 재생성된다.** `reports/*.csv` 를 다시 만들어
   행 단위로 맞춰 본다. 이게 진짜 검사다 — 1번은 코드가 자기 자신과 같은지만
   보고, 2번은 **발표에 쓰는 파일**이 지금 코드에서 나오는지를 본다.
   어긋나면 어느 컬럼이 몇 행 다른지 낸다.

3. **중복 누적 방지.** 같은 run 디렉터리를 두 번 넘겨 계산한다. 분모가 두 배가
   되면 안 된다(`load_videos` 가 video_id 를 키로 두는 이유). 실수로 같은 run 을
   두 번 넘기는 건 실제로 잘 일어난다.

4. **입력·설정 버전 추적.** 입력 파일과 코드 파일의 sha256, 임계값
   (TAU · DIFFUSION_TAU · METRIC_VERSION), 주제 사전 해시를 매니페스트로 남긴다.
   임계값이 바뀌면 해시가 바뀌므로 "어느 설정으로 낸 숫자인가"를 되짚을 수 있다.

사용법:
    python reproduce.py data/panel/run_A data/panel/run_B ...
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import judge
import topics
import trend

csv.field_size_limit(10 ** 8)

# 재생성해서 대조할 커밋된 산출물
TREND_CSV = Path("reports/trend_sunscreen_v0.2.csv")
JUDGEMENT_CSV = Path("reports/trend_judgement_v0.2.csv")

CODE_FILES = ["trend.py", "judge.py", "topics.py", "to_common_schema.py"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def sha_file(path: Path) -> str:
    return sha(path.read_bytes()) if path.exists() else "없음"


def to_csv(rows: list[dict]) -> str:
    """비교용 정규화 CSV. 컬럼 순서까지 고정해야 해시가 의미를 가진다."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def compute(run_dirs: list[Path], panel: dict[str, str]) -> tuple[list[dict], list[dict]]:
    rows = []
    for source in ("video", "comment"):
        rows.extend(trend.build_rows(run_dirs, panel, source))
    judged, _ = judge.judge(list(csv.DictReader(io.StringIO(to_csv(rows)))))
    return rows, judged


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def compare(name: str, made: list[dict], committed: Path) -> bool:
    """행 단위 대조. 다르면 어느 컬럼이 몇 행 다른지 낸다."""
    if not committed.exists():
        print(f"  [{name}] 커밋된 파일이 없다: {committed}")
        return False
    old = read_csv(committed)
    if len(old) != len(made):
        print(f"  [{name}] 행 수가 다르다 — 커밋 {len(old)} vs 재생성 {len(made)}")
        return False

    key = ("quarter", "topic_id", "source")
    old_by = {tuple(r[k] for k in key): r for r in old}
    diffs: dict[str, int] = {}
    missing = 0
    for row in made:
        k = tuple(str(row[c]) for c in key)
        prev = old_by.get(k)
        if prev is None:
            missing += 1
            continue
        for column, value in row.items():
            if column not in prev:
                continue
            # 커밋 파일은 전부 문자열이다. None 은 빈 칸으로 나가므로 그렇게 맞춘다
            mine = "" if value is None else str(value)
            if mine != prev[column]:
                diffs[column] = diffs.get(column, 0) + 1
    if missing:
        print(f"  [{name}] 커밋 파일에 없는 키 {missing}개")
    if diffs:
        for column, n in sorted(diffs.items(), key=lambda kv: -kv[1]):
            print(f"  [{name}] {column} : {n}행 불일치")
        return False
    print(f"  [{name}] {len(made)}행 전부 일치")
    return not missing


def run(run_dirs: list[Path], panel_csv: Path, out: Path) -> int:
    panel = trend.load_panel(panel_csv)

    print("1. 동일 입력 두 번")
    rows_a, judged_a = compute(run_dirs, panel)
    rows_b, judged_b = compute(run_dirs, panel)
    same_twice = (to_csv(rows_a) == to_csv(rows_b)
                  and to_csv(judged_a) == to_csv(judged_b))
    print(f"  지표 {sha(to_csv(rows_a).encode())} / 판정 {sha(to_csv(judged_a).encode())} "
          f"— {'일치' if same_twice else '불일치'}")

    print("2. 커밋된 산출물 재생성")
    trend_ok = compare("지표", rows_a, TREND_CSV)
    judge_ok = compare("판정", judged_a, JUDGEMENT_CSV)

    print("3. 중복 누적 방지 (같은 run 을 두 번 넘김)")
    rows_dup, judged_dup = compute(list(run_dirs) + list(run_dirs), panel)
    dup_ok = to_csv(rows_a) == to_csv(rows_dup) and to_csv(judged_a) == to_csv(judged_dup)
    docs_a = {r["quarter"]: r["quarter_documents"] for r in rows_a}
    docs_d = {r["quarter"]: r["quarter_documents"] for r in rows_dup}
    print(f"  분기 문서 수 합계 {sum(docs_a.values()):,} -> {sum(docs_d.values()):,} "
          f"— {'같다' if dup_ok else '늘었다(중복 누적)'}")

    manifest = {
        "tool": "reproduce.py",
        "run_dirs": [str(p) for p in run_dirs],
        "panel": str(panel_csv),
        "settings": {
            "metric_version": trend.METRIC_VERSION,
            "tau": judge.TAU,
            "diffusion_tau": judge.DIFFUSION_TAU,
            "min_document_count": trend.MIN_DOCUMENT_COUNT,
            "shorts_max_seconds": trend.SHORTS_MAX_SECONDS,
            "trend_topics": len(trend.TREND_TOPICS),
            # 사전이 바뀌면 모든 숫자가 바뀐다. 해시로 되짚을 수 있게 남긴다
            "topic_dictionary_sha": sha(
                json.dumps(topics.TOPICS, ensure_ascii=False, sort_keys=True).encode()),
        },
        "inputs": {
            **{str(p / "processed" / "videos.csv"): sha_file(p / "processed" / "videos.csv")
               for p in run_dirs},
            **{str(p / "processed" / "comments.csv"): sha_file(p / "processed" / "comments.csv")
               for p in run_dirs},
            str(panel_csv): sha_file(panel_csv),
        },
        "code": {f: sha_file(Path(f)) for f in CODE_FILES},
        "outputs": {
            "trend_rows": len(rows_a),
            "trend_sha": sha(to_csv(rows_a).encode()),
            "judgement_rows": len(judged_a),
            "judgement_sha": sha(to_csv(judged_a).encode()),
        },
        "checks": {
            "same_input_same_output": same_twice,
            "committed_trend_reproduced": trend_ok,
            "committed_judgement_reproduced": judge_ok,
            "no_duplicate_accumulation": dup_ok,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [k for k, v in manifest["checks"].items() if not v]
    print()
    print(f"{out} 저장")
    if failed:
        print(f"실패한 검사: {', '.join(failed)}")
        return 1
    print("네 가지 검사 전부 통과. 이 산출물은 재현된다.")
    return 0


def demo() -> None:
    # 해시는 컬럼 순서에 민감해야 한다. 아니면 재현성 검사가 통과만 하는 장식이 된다
    assert to_csv([{"a": 1, "b": 2}]) != to_csv([{"b": 2, "a": 1}])
    assert to_csv([{"a": 1}]) == to_csv([{"a": 1}])
    # None 은 빈 칸으로 나간다 — 커밋 파일과 맞추는 규칙. 단일 컬럼일 때만
    # csv 모듈이 빈 줄과 구분하려고 따옴표를 붙인다
    assert to_csv([{"a": None}]).splitlines()[1] in ('""', "")
    assert to_csv([{"a": None, "b": 1}]).splitlines()[1] == ",1"
    assert sha(b"x") != sha(b"y") and len(sha(b"x")) == 16
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dirs", nargs="*", type=Path)
    p.add_argument("--panel", type=Path, default=Path("seeds/channels_v1.csv"))
    p.add_argument("--out", type=Path, default=Path("reports/reproducibility.json"))
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
