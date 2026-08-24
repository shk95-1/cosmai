#!/usr/bin/env python3
"""검색 평가. 검색기가 좋은지 나쁜지를 재는 채점기다.

왜 따로 만드나. 검색기만 있으면 결과를 보고 "그럴듯하다"고 말할 수밖에 없다.
어제 `judge.py`(판정)와 `backtest.py`(채점)를 따로 둔 것과 같은 구조다. 채점기가
없으면 하이브리드 가중치를 무슨 근거로 고를지 알 수 없다.

**정답은 공짜로 있다.** `common/mention.csv` 가 (문서, 주제) 105,358쌍이다.
`match_topics()` 가 만든 것이므로 사람이 라벨을 달 필요가 없다.

두 가지 모드로 재고, 둘이 서로 다른 질문이다.

  literal   질의 = 주제 별칭 하나, 정답 = 그 주제가 붙은 문서 전부
            → **하한 확인용.** 정답 자체가 문자열 매칭으로 만들어졌으니 BM25 가
              당연히 잘한다. 여기서 못하면 토큰화가 깨진 것이다(프리픽스 누락,
              사전 미적용, 정규화 불일치). 성능 자랑용이 아니라 고장 감지용이다.

  heldout   질의 = 별칭 A, 정답 = **A 의 토큰이 하나도 없는** 같은 주제 문서
            → **진짜 측정.** "하얗게"로 물어서 그 말이 안 들어간 백탁 문서를
              찾아낼 수 있는가. BM25 는 여기서 거의 0 이 나오는 게 정상이고,
              **이 0 이 벡터가 넘어야 하는 선이다.**

heldout 에서 후보를 줄이지 않는 게 중요하다. 두 방식이 같은 후보·같은 정답으로
경쟁해야 점수를 비교할 수 있다. A 가 들어간 문서를 BM25 가 가져오면 그건 정답이
아니므로 그대로 감점된다 — 그게 맞다.

지표. 정답 집합이 수천~수만이라 Recall@10 은 정의상 0.1% 를 못 넘어 쓸 수 없다.
그래서 상위 k 만 본다.

  P@10     상위 10건 중 정답 비율
  Hit@10   상위 10건에 정답이 하나라도 있는 질의 비율
  MRR@10   첫 정답이 몇 번째에 나오나 (1/순위)

**주의 — 이 결과로 파라미터를 만지면 그때부터 이 숫자는 성능이 아니다.** 자동 라벨은
가중치 고르는 데 쓰고, 사람이 만든 골든셋은 최종 보고에 한 번만 쓴다. 어제 후향
검증에서 기저율을 같이 낸 것과 같은 이유다.

사용법:
    python retrieval_eval.py
    python retrieval_eval.py --mode heldout
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

import bm25
from topics import TOPICS

csv.field_size_limit(10 ** 8)

K = 10
FIELDS = ["mode", "engine", "topic_id", "query", "gold_size", "retrieved",
          "p_at_k", "mrr", "hit"]


def load_gold(common: Path) -> dict[str, set[str]]:
    """topic_id -> doc_id 집합. match_topics 가 만든 라벨을 그대로 쓴다."""
    gold: dict[str, set[str]] = defaultdict(set)
    with (common / "mention.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            gold[row["topic_id"]].add(row["doc_id"])
    return gold


def docs_with_tokens(index: bm25.Index, query: str) -> set[str]:
    """질의 토큰이 하나라도 들어 있는 문서. heldout 의 정답에서 빼는 데 쓴다.

    부분문자열로 빼면 안 된다. `하얘서` 는 Kiwi 가 `하얗` 으로 주므로 글자로는
    안 겹치는데 토큰으로는 겹친다. 색인이 실제로 쓰는 단위로 빼야 한다.
    """
    # postings 를 그대로 읽으므로 재토큰화 비용이 없다
    found: set[str] = set()
    for term in set(bm25.tokenize(query)):
        for i, _tf in index.postings.get(term, ()):
            found.add(index.doc_ids[i])
    return found


def queries(mode: str) -> list[tuple[str, str]]:
    """(topic_id, 질의). 질의는 주제 별칭이다 — 사람이 라벨을 만들지 않는다."""
    out = []
    for entry in TOPICS:
        if not entry["trend_use"]:
            continue                      # 판정에 안 쓰는 주제는 평가에서도 뺀다
        aliases = entry["ko"] + entry["latin"]
        if mode == "heldout" and len(aliases) < 2:
            continue                      # 별칭이 하나면 뺄 게 없다 (혼합자차)
        for alias in aliases:
            out.append((entry["topic"], alias))
    return out


def to_docs(ids: list[str], k: int) -> list[str]:
    """검색 결과를 **문서 단위로** 바꾼다. 이게 없으면 벡터 점수가 항상 0 이다.

    정답(`mention.csv`)은 `doc_id` 인데 청크 색인은 `chunk_id`(`{doc_id}#{ordinal}`)를
    돌려준다. 형식이 달라 **한 번도 일치하지 않는다** — 모델이 아무리 잘 찾아도
    0.000 이 나온다. 실측으로 벡터가 찾아온 상위 5건 중 3번째가 정답이었는데
    `#0` 이 붙어 있어서 못 맞춘 것으로 세어졌다.

    한 문서의 여러 청크가 상위에 들어오면 한 번만 센다. 안 그러면 긴 문서 하나가
    상위 10칸을 차지하고 P@10 이 부풀거나 깎인다.
    """
    out, seen = [], set()
    for chunk_id in ids:
        doc_id = chunk_id.rsplit("#", 1)[0]
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
        if len(out) >= k:
            break
    return out


def score(ranked: list[str], gold: set[str]) -> tuple[float, float, bool]:
    """(P@k, MRR@k, Hit@k). 정답이 비면 이 질의는 건너뛰어야 하므로 호출 전에 막는다."""
    hits = [i for i, doc in enumerate(ranked, 1) if doc in gold]
    p = len(hits) / len(ranked) if ranked else 0.0
    mrr = 1 / hits[0] if hits else 0.0
    return p, mrr, bool(hits)


def run(common: Path, mode: str, out: Path, sources: list[str] | None,
        no_cache: bool, engine: str, chunks: list[Path]) -> int:
    # 색인은 heldout 의 정답 계산(질의 토큰이 든 문서 빼기)에 필요하므로 항상 만든다.
    # 벡터만 재는 경우에도 정답 정의는 어휘 기준이어야 셋을 같은 기준으로 비교한다.
    index, _origin = bm25.build(common, sources,
                                None if no_cache else Path(".cache/bm25"), chunks)
    if engine == "bm25":
        search = index.search
    else:
        import hybrid
        search, _o = hybrid.make_engine(engine, common, chunks, no_cache=no_cache)

    gold_all = load_gold(common)
    print(f"색인 {index.n:,}개 문서 · 고유 토큰 {len(index.postings):,}")
    print(f"검색기 {engine} · 모드 {mode} · 질의 {len(queries(mode))}개")
    print()

    rows = []
    for topic, alias in queries(mode):
        gold = set(gold_all.get(topic, ()))
        if mode == "heldout":
            gold -= docs_with_tokens(index, alias)
        if not gold:
            continue                      # 정답이 없는 질의는 채점 불가
        # 문서 단위로 맞춰야 한다. 청크가 여러 개면 넉넉히 받아 K개 문서로 줄인다
        ranked = to_docs([doc for doc, _s in search(alias, K * 5)], K)
        p, mrr, hit = score(ranked, gold)
        rows.append({"mode": mode, "engine": engine, "topic_id": topic, "query": alias,
                     "gold_size": len(gold), "retrieved": len(ranked),
                     "p_at_k": round(p, 3), "mrr": round(mrr, 3),
                     "hit": "true" if hit else "false"})

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    if not rows:
        print("채점 가능한 질의가 없다")
        return 1

    print(f"{'주제':<20}{'질의':<14}{'정답수':>8}{'P@10':>7}{'MRR':>7}")
    for r in rows:
        print(f"{r['topic_id']:<20}{r['query']:<14}{r['gold_size']:>8,}"
              f"{r['p_at_k']:>7.2f}{r['mrr']:>7.2f}")
    print()
    print(f"질의 {len(rows)}개 — P@{K} {statistics.fmean(r['p_at_k'] for r in rows):.3f} · "
          f"MRR@{K} {statistics.fmean(r['mrr'] for r in rows):.3f} · "
          f"Hit@{K} {100 * sum(r['hit'] == 'true' for r in rows) / len(rows):.0f}%")
    print()
    if mode == "literal":
        print("이 숫자는 성능이 아니라 고장 감지용이다. P@10 이 0.9 밑이면 토큰화를 의심한다.")
    elif engine == "bm25":
        print("이 숫자가 벡터가 넘어야 하는 선이다. BM25 는 글자가 안 겹치면 못 찾는다.")
    else:
        # 채택 기준은 미리 정해 뒀다. 결과를 보고 기준을 만들면 그건 성능이 아니다
        print("BM25 의 heldout 은 구조적으로 0.000 이다. "
              + ("0 을 넘었으므로 임베딩이 기여한 몫이 있다."
                 if any(r["hit"] == "true" for r in rows)
                 else "0 을 못 넘었으므로 붙일 이유가 없다."))
    print(f"{out} 저장")
    return 0


def demo() -> None:
    assert score(["a", "b", "c"], {"a"}) == (1 / 3, 1.0, True)
    assert score(["x", "a"], {"a"}) == (0.5, 0.5, True)
    assert score(["x", "y"], {"a"}) == (0.0, 0.0, False)
    assert score([], {"a"}) == (0.0, 0.0, False)
    # heldout 은 별칭이 2개 이상인 주제만 낸다
    assert not any(t == "혼합자차" for t, _ in queries("heldout"))
    assert any(t == "혼합자차" for t, _ in queries("literal"))
    # 판정에 안 쓰는 주제(선크림·추천_재구매)는 평가에서도 빠진다
    assert not any(t == "선크림" for t, _ in queries("literal"))

    # 청크 id 를 문서 id 로 되돌려야 정답과 형식이 맞는다. 이걸 안 하면 항상 0 이다
    assert to_docs(["a:1#0", "a:1#1", "b:2#0"], 2) == ["a:1", "b:2"]
    assert to_docs(["a#0"], 5) == ["a"]
    assert to_docs(["a", "b"], 5) == ["a", "b"], "# 이 없어도 그대로 통과해야 한다"
    assert to_docs([], 3) == []
    # 한 문서의 여러 청크는 한 번만. 안 그러면 긴 문서가 상위를 차지한다
    assert to_docs(["x#0", "x#1", "x#2", "y#0"], 10) == ["x", "y"]

    index = bm25.Index(["a", "b", "c"],
                       ["백탁 심하다", "하얘서 싫다", "끈적임 유분"])
    # `하얗게` 는 b 에 글자로 없지만 토큰(하얗)으로는 있다. 부분문자열로 빼면 놓친다
    assert "b" in docs_with_tokens(index, "하얗게"), bm25.tokenize("하얘서 싫다")
    assert "c" not in docs_with_tokens(index, "하얗게")
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--common", type=Path, default=Path("common"))
    p.add_argument("--mode", choices=["literal", "heldout"], default="literal")
    p.add_argument("--source", action="append")
    p.add_argument("--out", type=Path)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--engine", choices=["bm25", "vector", "hybrid"], default="bm25")
    p.add_argument("--chunks", action="append", type=Path,
                   default=[Path("reports/chunks_ingredient_mfds.csv")])
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    out = a.out or Path(f"reports/retrieval_eval_{a.mode}_{a.engine}.csv")
    return run(a.common, a.mode, out, a.source, a.no_cache, a.engine, a.chunks)


if __name__ == "__main__":
    raise SystemExit(main())
