#!/usr/bin/env python3
"""성분표를 임베딩 청크로 만든다. `chunks.py` 의 성분 쪽 짝이다.

왜 별도 파일인가. 유튜브는 공통 스키마에 `doc_id` 가 이미 있지만 **성분표에는
없다.** 그래서 `chunks.py` 를 성분표에 그냥 돌릴 방법이 없다(돌리면 유튜브가
나온다). `doc_id` 규칙을 정하는 것이 이 파일의 절반이다.

두 종류를 낸다. 카드가 두 방향을 다 묻기 때문이다.

    ingredient_product   제품 하나 = 청크. "이 조합의 제품" 을 찾는 데 쓴다
    ingredient_term      성분 하나 = 청크. "이 성분을 쓰는 제품" 을 찾는 데 쓴다

**배합 순위를 개별 순번으로 적는다.** 전성분표는 함량 순으로 쓰므로 순위가 곧
함량 정보다. 범위만("1~25순위") 적으면 정제수가 1위인지 25위인지 알 수 없다.
`MASTER_REPORT` §5-② 의 발견 — *"미백만 71%가 고함량 구간에 배치"* — 이 정확히
"몇 위인지"로 갈리는 결과이므로, 순번이 없으면 그 발견을 검색으로 재현할 수 없다.
(수호님 제안)

`doc_id` 는 이렇게 만든다.

    ingredient_product:{제품명 sha1 앞 12자}   제품명에 대괄호·슬래시가 많아 그대로 쓸 수 없다
    ingredient_term:{성분명}                   성분명은 공백 제거 후 고유하고 읽기 쉽다

**묶음 페이지를 걸러야 한다.** 올리브영 상품의 상당수가 `기획/N종 택1` 이고, 그
페이지의 전성분은 여러 SKU 목록을 이어붙인 것이다. 실측으로 성분 100개를 넘는
제품이 21개이고 최대 385행이다(같은 성분이 한 제품 안에서 12번 반복). 이걸
제품 하나로 읽으면 "이 제품에 무엇이 들어 있나" 가 틀린다. 그래서
  - 제품 안에서 성분을 중복 제거하고
  - 고유 성분이 `BUNDLE_INGREDIENTS` 를 넘으면 **묶음 페이지로 표시**한다
  - 성분별 채택 제품 수에서는 묶음 페이지를 **뺀다**

`fix` 에 "집계에서 제외할 것" 이 붙은 행도 뺀다. 뭉친 문자열이 해독되지 않은
채로 남은 것이라 성분 하나가 아니다(실측 22행).

정규화는 `trend.normalize_text` 하나만 쓴다. 소스마다 정규화가 다르면 소스 간
비교가 무의미해진다 — 팀 합의 사항이다.

사용법:
    python ingredient_chunks.py
    python chunks.py --validate reports/chunks_ingredient.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from chunks import FIELDS, check_rows, split_text
from trend import normalize_text

csv.field_size_limit(10 ** 8)

# 이 개수를 넘으면 한 제품이 아니라 묶음 페이지로 본다. 실측 분포에서 잡았다 —
# 제품당 고유 성분이 중앙 49 · 75분위 59 이고, 100 을 넘는 21개가 전부 `기획`·`택1`
# 페이지였다. 80 은 그 사이에서 정상 제품의 꼬리를 남기는 값이다.
BUNDLE_INGREDIENTS = 80
EXCLUDE_MARK = "제외"        # fix 컬럼에 이 말이 있으면 집계에서 뺀다
TOP_PRODUCTS = 12            # 성분 청크에 이름을 몇 개까지 적을지
TOP_COMPANIONS = 15          # 함께 쓰이는 성분 몇 개까지


def doc_id_for(kind: str, name: str) -> str:
    if kind == "ingredient_term":
        return f"ingredient_term:{name}"
    # 제품명에 대괄호·슬래시·공백이 많아 id 로 쓸 수 없다. 해시는 입력이 같으면 같다
    return f"ingredient_product:{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}"


def load(path: Path) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """(제품 -> {brand, 성분 순서 목록}, 성분 -> 기능 집합).

    알맹이는 `load_rows` 다. 파일을 읽는 부분만 여기 있다.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return load_rows(list(csv.DictReader(handle)))


def product_chunks(products: dict[str, dict]) -> tuple[list[dict], set[str]]:
    """제품 하나 = 청크. 묶음 페이지는 본문에 그렇게 적는다."""
    rows, bundles = [], set()
    for name, entry in products.items():
        ingredients = entry["ingredients"]
        bundle = len(ingredients) > BUNDLE_INGREDIENTS
        if bundle:
            bundles.add(name)
        head = (f"[여러 제품이 섞인 묶음 페이지] " if bundle else "")
        # 순번을 붙인다. 전성분표는 함량 순이므로 순위가 함량 정보다
        listed = ", ".join(f"{i}위 {name_}" for i, name_ in enumerate(ingredients, 1))
        body = (f"{head}{entry['brand']} {name}. "
                f"전성분 {len(ingredients)}종: {listed}")
        doc_id = doc_id_for("ingredient_product", name)
        for ordinal, piece in enumerate(split_text(normalize_text(body))):
            rows.append({"chunk_id": f"{doc_id}#{ordinal}", "doc_id": doc_id,
                         "source": "ingredient_product", "ordinal": ordinal,
                         "text": piece})
    return rows, bundles


def term_chunks(products: dict[str, dict], functions: dict[str, set[str]],
                bundles: set[str]) -> list[dict]:
    """성분 하나 = 청크. 채택 제품 수에서 묶음 페이지를 뺀다."""
    used: dict[str, list[str]] = defaultdict(list)
    companions: dict[str, Counter] = defaultdict(Counter)
    ranks: dict[str, list[int]] = defaultdict(list)
    for name, entry in products.items():
        if name in bundles:
            continue                    # 묶음 페이지는 채택 통계에 넣지 않는다
        for position, ingredient in enumerate(entry["ingredients"], 1):
            ranks[ingredient].append(position)
        for ingredient in entry["ingredients"]:
            used[ingredient].append(name)
        for ingredient in entry["ingredients"]:
            for other in entry["ingredients"]:
                if other != ingredient:
                    companions[ingredient][other] += 1

    total = len(products) - len(bundles)
    rows = []
    for ingredient, names in used.items():
        share = 100 * len(names) / total if total else 0.0
        function = ", ".join(sorted(functions.get(ingredient, ()))) or "미기재"
        near = ", ".join(w for w, _n in companions[ingredient].most_common(TOP_COMPANIONS))
        # 제품명이 길어 다 넣으면 청크가 쪼개진다. 개수는 숫자로 이미 밝혔으므로
        # 이름은 대표 몇 개만 적는다
        sample = ", ".join(n[:40] for n in sorted(names)[:TOP_PRODUCTS])
        # 배합 순위. 중앙값과 "상위 10위 이내" 비율을 같이 낸다 — 전자는 전형적
        # 위치, 후자는 고함량으로 쓰이는 빈도다. 둘이 갈리는 성분이 있다
        order = sorted(ranks[ingredient])
        median_rank = order[len(order) // 2]
        high = 100 * sum(1 for r in order if r <= 10) / len(order)
        body = (f"{ingredient}. 기능: {function}. "
                f"선케어 {total}개 중 {len(names)}개({share:.1f}%)에 포함. "
                f"배합 순위 중앙 {median_rank}위 (최고 {order[0]}위 · 최저 {order[-1]}위), "
                f"상위 10위 이내로 쓰이는 비율 {high:.0f}%. "
                f"함께 쓰이는 성분: {near}. 사용 제품: {sample}")
        doc_id = doc_id_for("ingredient_term", ingredient)
        for ordinal, piece in enumerate(split_text(normalize_text(body))):
            rows.append({"chunk_id": f"{doc_id}#{ordinal}", "doc_id": doc_id,
                         "source": "ingredient_term", "ordinal": ordinal,
                         "text": piece})
    return rows


def run(source: Path, out: Path) -> int:
    products, functions = load(source)
    pr, bundles = product_chunks(products)
    tr = term_chunks(products, functions, bundles)
    rows = pr + tr

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    counts = [len(e["ingredients"]) for e in products.values()]
    unique = {i for e in products.values() for i in e["ingredients"]}
    print(f"제품 {len(products):,} · 고유 성분 {len(unique):,}")
    print(f"제품당 고유 성분 중앙 {statistics.median(counts):.0f} · 최대 {max(counts)}")
    print(f"묶음 페이지로 판정 {len(bundles)}개 (성분 {BUNDLE_INGREDIENTS} 초과) — "
          f"채택 통계에서 제외")
    for name in sorted(bundles)[:5]:
        print(f"    {len(products[name]['ingredients']):>4}종  {name[:56]}")
    print()
    print(f"청크 {len(rows):,} — 제품 {len(pr):,} · 성분 {len(tr):,}")
    lengths = sorted(len(r["text"]) for r in rows)
    print(f"청크 길이 중앙 {lengths[len(lengths) // 2]}자 · 최대 {lengths[-1]}자")

    problems, _per, _len, _docs = check_rows(rows)
    print()
    if problems:
        print(f"[실패] 계약 위반 {len(problems)}종")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"[통과] chunks.py 계약을 지켰다 — {out} 저장")
    return 0


def demo() -> None:
    # doc_id 는 입력이 같으면 같아야 한다. 재현이 안 되면 벡터를 다시 못 붙인다
    assert doc_id_for("ingredient_term", "판테놀") == "ingredient_term:판테놀"
    a = doc_id_for("ingredient_product", "[기획] 브랜드 선크림 50ml")
    assert a == doc_id_for("ingredient_product", "[기획] 브랜드 선크림 50ml")
    assert a != doc_id_for("ingredient_product", "[기획] 브랜드 선크림 30ml")
    assert a.startswith("ingredient_product:") and len(a.split(":")[1]) == 12

    rows = [
        {"product_name": "제품가", "brand": "브", "ingredient": "정제수",
         "function": "solvent", "fix": ""},
        # 같은 제품 안 중복 — 묶음 페이지에서 실제로 일어난다
        {"product_name": "제품가", "brand": "브", "ingredient": "정제수",
         "function": "solvent", "fix": ""},
        {"product_name": "제품가", "brand": "브", "ingredient": "판테놀",
         "function": "skin conditioning", "fix": ""},
        {"product_name": "제품나", "brand": "브", "ingredient": "판테놀",
         "function": "humectant", "fix": ""},
        # 해독 실패 행은 성분 하나가 아니다
        {"product_name": "제품나", "brand": "브", "ingredient": "정제수글리세린판테놀",
         "function": "", "fix": "분해 실패 (해독률 87%) — 집계에서 제외할 것"},
        # 공백은 제거한다. 성분표 원본에 공백 손상이 있었다
        {"product_name": "제품나", "brand": "브", "ingredient": "에칠헥실 트리아존",
         "function": "uv filter", "fix": "공백 제거"},
    ]
    products, functions = load_rows(rows)
    assert products["제품가"]["ingredients"] == ["정제수", "판테놀"], products["제품가"]
    assert "정제수글리세린판테놀" not in products["제품나"]["ingredients"]
    assert "에칠헥실트리아존" in products["제품나"]["ingredients"]
    # 기능은 여러 행에서 모인다
    assert functions["판테놀"] == {"skin conditioning", "humectant"}

    pr, bundles = product_chunks(products)
    assert not bundles and {r["source"] for r in pr} == {"ingredient_product"}
    tr = term_chunks(products, functions, bundles)
    panthenol = next(r for r in tr if r["doc_id"] == "ingredient_term:판테놀")
    assert "2개 중 2개(100.0%)" in panthenol["text"], panthenol["text"]
    # 배합 순위가 본문에 있어야 한다. 없으면 고함량 질문에 답할 수 없다
    assert "배합 순위 중앙" in panthenol["text"], panthenol["text"]
    first = next(r for r in pr if r["ordinal"] == 0
                 and r["doc_id"] == doc_id_for("ingredient_product", "제품가"))
    assert "1위 정제수" in first["text"] and "2위 판테놀" in first["text"], first["text"]

    # 묶음 페이지는 채택 통계에서 빠져야 한다
    big = {"큰묶음": {"brand": "브", "ingredients": [f"성분{i}" for i in range(200)]},
           "정상": {"brand": "브", "ingredients": ["성분0"]}}
    _pr, bundles = product_chunks(big)
    assert bundles == {"큰묶음"}
    got = term_chunks(big, {}, bundles)
    first = next(r for r in got if r["doc_id"] == "ingredient_term:성분0")
    assert "1개 중 1개(100.0%)" in first["text"], first["text"]
    assert not any(r["doc_id"] == "ingredient_term:성분199" for r in got)
    # 묶음 페이지 자체는 청크로 남는다. 표시만 붙인다
    marked = next(r for r in _pr if r["ordinal"] == 0
                  and r["doc_id"] == doc_id_for("ingredient_product", "큰묶음"))
    assert "묶음 페이지" in marked["text"]
    print("demo ok")


def load_rows(rows: list[dict]) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """제품 안에서 성분을 중복 제거한다. 묶음 페이지에서 같은 성분이 여러 번
    나오는데 그걸 남기면 성분 수가 부풀고 청크 본문이 같은 말을 반복한다.

    `fix` 에 제외 표시가 붙은 행은 뺀다 — 뭉친 문자열이 해독되지 않은 채로 남은
    것이라 성분 하나가 아니다(실측 22행).
    """
    products: dict[str, dict] = {}
    functions: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if EXCLUDE_MARK in (row.get("fix") or ""):
            continue
        name = normalize_text(row["product_name"])
        ingredient = normalize_text(row["ingredient"]).replace(" ", "")
        if not name or not ingredient:
            continue
        entry = products.setdefault(name, {"brand": normalize_text(row["brand"]),
                                           "ingredients": []})
        if ingredient not in entry["ingredients"]:
            entry["ingredients"].append(ingredient)
        for function in (row.get("function") or "").split(","):
            function = function.strip()
            if function:
                functions[ingredient].add(function)
    return products, functions


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path,
                   default=Path("reports/ingredient_normalized.csv"),
                   help="normalize_ingredients.py 의 결과. 원본은 공백 손상이 있다")
    p.add_argument("--out", type=Path, default=Path("reports/chunks_ingredient.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    return run(a.source, a.out)


if __name__ == "__main__":
    raise SystemExit(main())
