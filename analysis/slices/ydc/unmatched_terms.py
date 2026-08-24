#!/usr/bin/env python3
"""사전에 걸리지 않은 고빈도 명사를 뽑는다. 사전의 천장을 사람이 보게 만드는 목록이다.

왜. 기획안 §11 에 최대 한계를 이렇게 적어 뒀다 — "사전에 없는 성분은 관측되지 않는다.
신규 등장 판정이 잡는 것은 사전에 이미 있는 13개 중 새로 뜬 것뿐이다." R&D 기회 탐색에서
이건 실질적 제약이고, 대응책으로 약속한 것이 이 목록이다.

무엇을 하나. 판정 모집단의 문서를 형태소 분석해 명사를 뽑고, 그중 **우리 사전에 걸리지
않는 것**만 남긴다. 자동으로 사전에 넣지 않는다. 사람이 보고 `topics.py` 를 고칠지
판단하는 재료다.

**빈도만으로는 쓸 수 없다.** 처음에 그렇게 뽑아 보니 상위가 피부·제품·감사·언니·구매로
채워졌다. 선크림 영상이라서 많은 말이 아니라 한국어 댓글이라서 많은 말이다.
그래서 composition 과 같은 방식을 쓴다 — 절대 빈도가 아니라 **대조군 대비 비중**이다.

  선크림군   product 34채널 장문 중 선크림 언급 영상과 그 댓글
  대조군     같은 채널의 장문 중 **선크림을 언급하지 않은** 영상
  lift       선크림군 등장 문서 비율 / 대조군 등장 문서 비율

lift 가 1 근처면 선크림과 무관한 일반어다. 높을수록 선크림 문맥에 특이한 말이다.
댓글에는 대조군이 없다(댓글은 주제 사전에 걸린 영상에서만 수집했다). 그래서 lift 는
영상으로 계산하고 댓글 수는 참고로 함께 낸다.

"사전에 걸리지 않는다"의 판정은 `match_topics(명사)` 가 빈 결과인지로 한다. 별칭 판정을
따로 만들면 본 파이프라인과 어긋나므로 같은 함수를 쓴다.

형태소 분석은 Kiwi 를 쓰고 사용자 사전(`seeds/user_dictionary.tsv`)을 반드시 적용한다.
사전 없이 돌리면 `백탁` 이 `백`+`탁`, `눈시림` 이 `눈`+`시리`+`ㅁ` 으로 쪼개져
결과가 쓸모없어진다.

브랜드·제품명은 제거하지 않고 표시만 한다. 신규 브랜드의 등장도 관측 대상이므로
사람이 보고 판단하는 게 맞다.

사용법:
    python unmatched_terms.py
"""
from __future__ import annotations

import argparse
import collections
import csv
from pathlib import Path

from topics import match_topics

csv.field_size_limit(10 ** 8)

NOUN_TAGS = {"NNG", "NNP", "SL"}   # 일반명사·고유명사·외국어
MIN_LENGTH = 2                     # 한 글자 명사는 잡음이 많다
MIN_DOCS = 5                       # 우리 표본 기준과 같게 둔다
MIN_LIFT = 2.0                     # 대조군 대비 이 배수 미만은 일반어로 본다
FIELDS = ["noun", "lift", "sun_video_docs", "other_video_docs", "comment_docs",
          "quarters_present", "peak_quarter", "peak_docs", "inci_products",
          "looks_like_brand"]


def is_word(noun: str) -> bool:
    """자모 조각과 기호를 걸러낸다. 자모 반복이 명사로 잡혀 상위에 올라온다."""
    return all("가" <= c <= "힣" or c.isascii() for c in noun)


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as h:
        return list(csv.DictReader(h))


def quarter_of(published_at: str) -> str:
    year, month = published_at[:4], int(published_at[5:7])
    return f"{year}Q{(month - 1) // 3 + 1}"


def load_population(common: Path) -> list[tuple[str, str, str]]:
    """(bucket, quarter, text). bucket 은 sun_video / other_video / comment.

    선크림군은 판정 모집단과 같고, 대조군은 같은 채널의 선크림 아닌 장문이다.
    """
    product = {r["channel_id"] for r in read(common / "channel.csv")
               if r["panel_role"] == "product"}
    sunscreen = {m["doc_id"] for m in read(common / "mention.csv")
                 if m["topic_id"] == "선크림"}

    videos: dict[str, str] = {}
    docs: list[tuple[str, str, str]] = []
    comments: list[tuple[str, str]] = []
    with (common / "document.csv").open(encoding="utf-8-sig", newline="") as h:
        for row in csv.DictReader(h):
            if row["quality_flags"]:
                continue
            if (row["source"] == "youtube_video" and row["content_type"] == "video_long"
                    and row["channel_id"] in product):
                q = quarter_of(row["published_at"])
                if row["doc_id"] in sunscreen:
                    videos[row["source_item_id"]] = q
                    docs.append(("sun_video", q, row["text"]))
                else:
                    docs.append(("other_video", q, row["text"]))
            elif row["source"] == "youtube_comment":
                comments.append((row["parent_item_id"], row["text"]))
    # 댓글은 부모 영상의 분기에 배정한다. 자기 시각을 쓰면 분모가 정의되지 않는다.
    for parent, text in comments:
        if parent in videos:
            docs.append(("comment", videos[parent], text))
    return docs


def load_ingredient_names(path: Path | None) -> tuple[set[str], collections.Counter]:
    """(브랜드·제품명, 성분명별 제품 수).

    성분명 쪽이 이 스크립트의 핵심이다. 식약처·올리브영 성분표에 실제로 있는 말이
    우리 사전에 없다면 그건 바로 추가 후보다. 브랜드는 반대로 추가하면 안 되는 쪽이다.
    """
    if not path or not path.exists():
        return set(), collections.Counter()
    brands, inci = set(), collections.defaultdict(set)
    for row in read(path):
        for field in ("brand", "product_name"):
            value = (row.get(field) or "").strip()
            if value:
                brands.add(value.replace(" ", ""))
        name = (row.get("ingredient") or "").strip().replace(" ", "")
        if name:
            inci[name].add(row.get("product_name") or "")
    return brands, collections.Counter({k: len(v) for k, v in inci.items()})


def run(common: Path, ingredients: Path | None, dictionary: Path,
        stopwords: Path, out: Path, top: int) -> None:
    from kiwipiepy import Kiwi

    kiwi = Kiwi()
    if dictionary.exists():
        kiwi.load_user_dictionary(str(dictionary))
    else:
        print(f"[경고] 사용자 사전이 없다: {dictionary}. 백탁·눈시림이 쪼개진다")
    stop = {l.strip() for l in stopwords.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")} if stopwords.exists() else set()
    brands, inci = load_ingredient_names(ingredients)

    channels = {r["channel_title"].replace(" ", "")
                for r in read(common / "channel.csv") if r.get("channel_title")}

    docs = load_population(common)
    counts = collections.Counter(d[0] for d in docs)
    print(f"선크림 장문 {counts['sun_video']:,} · 대조군 장문 {counts['other_video']:,} · "
          f"댓글 {counts['comment']:,}")

    per_source: dict[str, collections.Counter] = {
        "sun_video": collections.Counter(), "other_video": collections.Counter(),
        "comment": collections.Counter()}
    per_quarter: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    covered_cache: dict[str, bool] = {}

    for (source, quarter, text), tokens in zip(docs, kiwi.tokenize((d[2] for d in docs))):
        seen = set()
        for token in tokens:
            if token.tag not in NOUN_TAGS or len(token.form) < MIN_LENGTH:
                continue
            noun = token.form
            if noun in seen or noun in stop or noun.isdigit() or not is_word(noun):
                continue
            if noun.replace(" ", "") in channels:      # 채널명은 주제가 아니다
                continue
            covered = covered_cache.get(noun)
            if covered is None:
                # 본 파이프라인과 같은 함수로 판정한다. 별칭 규칙을 두 벌 두지 않는다.
                covered = bool(match_topics(noun, include_excluded=True))
                covered_cache[noun] = covered
            if covered:
                continue
            seen.add(noun)
        for noun in seen:
            per_source[source][noun] += 1
            per_quarter[noun][quarter] += 1

    n_sun = max(1, counts["sun_video"])
    n_other = max(1, counts["other_video"])
    rows = []
    for noun, sun in per_source["sun_video"].most_common():
        if sun < MIN_DOCS:
            continue
        other = per_source["other_video"][noun]
        # 대조군에 한 번도 없으면 분모가 0 이라 lift 가 무한이 된다.
        # 0 대신 1건으로 두어(라플라스 보정) 순위가 폭발하지 않게 한다.
        lift = (sun / n_sun) / (max(other, 1) / n_other)
        if lift < MIN_LIFT:
            continue
        quarters = per_quarter[noun]
        peak, peak_n = quarters.most_common(1)[0]
        rows.append({
            "noun": noun, "lift": round(lift, 2),
            "sun_video_docs": sun, "other_video_docs": other,
            "comment_docs": per_source["comment"][noun],
            "quarters_present": len(quarters),
            "peak_quarter": peak, "peak_docs": peak_n,
            # 성분표에 있는데 사전에 없으면 최우선 추가 후보다
            "inci_products": inci[noun.replace(" ", "")],
            # 브랜드는 정확히 일치할 때만 표시한다. 부분문자열로 보면 일반어까지 걸린다
            "looks_like_brand": "true" if noun.replace(" ", "") in brands else "",
        })
    rows.sort(key=lambda r: -r["lift"])

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"사전 밖 명사 중 선크림 문서 {MIN_DOCS}건 이상 & lift {MIN_LIFT} 이상 : {len(rows):,}종")
    print()
    print(f"{'명사':<16}{'lift':>7}{'선크림':>7}{'대조':>7}{'댓글':>7}"
          f"{'분기':>5}{'최다분기':>9}  브랜드")
    shown = 0
    for r in rows:
        if shown >= top:
            break
        shown += 1
        print(f"{r['noun']:<16}{r['lift']:>7.1f}{r['sun_video_docs']:>7}"
              f"{r['other_video_docs']:>7}{r['comment_docs']:>7}"
              f"{r['quarters_present']:>5}{r['peak_quarter']:>9}"
              f"  {'브랜드' if r['looks_like_brand'] else ''}")
    hits = sorted((r for r in rows if r["inci_products"]),
                  key=lambda r: -r["inci_products"])
    print()
    print(f"이 중 성분표에 실제로 있는 말 {len(hits)}종 — 사전 추가 최우선 후보")
    for r in hits[:20]:
        print(f"{r['noun']:<16}{r['lift']:>7.1f}{r['sun_video_docs']:>7}"
              f"{r['comment_docs']:>7}   성분표 {r['inci_products']:,}개 제품")
    print()
    print(f"{out} 저장 — 자동으로 사전에 넣지 않는다. 사람이 보고 topics.py 를 고친다")


def demo() -> None:
    assert quarter_of("2026-04-01T00:00:00Z") == "2026Q2"
    assert quarter_of("2023-12-31T23:59:59Z") == "2023Q4"
    # 사전에 이미 있는 말은 후보에서 빠져야 한다
    assert match_topics("백탁", include_excluded=True)
    assert match_topics("선크림", include_excluded=True)
    # 사전에 없는 말은 남아야 한다
    assert not match_topics("병원", include_excluded=True)
    assert not match_topics("가격", include_excluded=True)
    # 자모 조각은 명사 후보가 아니다
    assert is_word("백탁") and is_word("SPF") and is_word("판테놀")
    assert not is_word("ᅲᅲ") and not is_word("ᄒᄒ")
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--common", type=Path, default=Path("common"))
    p.add_argument("--ingredients", type=Path,
                   default=Path("reports/ingredient_normalized.csv"),
                   help="브랜드·제품명 표시용. 없으면 표시만 비워 둔다")
    p.add_argument("--dictionary", type=Path, default=Path("seeds/user_dictionary.tsv"))
    p.add_argument("--stopwords", type=Path, default=Path("seeds/stopwords_ko.txt"))
    p.add_argument("--out", type=Path, default=Path("reports/unmatched_terms.csv"))
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    run(a.common, a.ingredients, a.dictionary, a.stopwords, a.out, a.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
